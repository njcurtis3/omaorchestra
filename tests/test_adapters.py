import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import adapters, daemon, launch, procs
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry


class CodexTest(unittest.TestCase):
    codex = adapters.Codex()

    def test_command(self):
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_CODEX": "codex"}):
            self.assertEqual(self.codex.command("fix it", "S", "/w", model="gpt-5", permission_mode="on-request"),
                             ["codex", "-C", "/w", "-m", "gpt-5", "-a", "on-request", "--", "fix it"])

    def test_events(self):
        base = {"session_id": "cx1", "cwd": "/w", "model": "gpt-5", "transcript_path": None, "launch_id": "L1"}
        ask = self.codex.request_for({**base, "hook_event_name": "PermissionRequest", "tool_name": "shell",
                                      "tool_input": {"command": ["rm", "-rf", "build"]}}, (5, 50))
        self.assertEqual((ask["status"], ask["message"], ask["agent"], ask["model"], ask["launch_id"], ask["pid"]),
                         ("needs-input", "Codex asks to use shell: rm -rf build", "codex", "gpt-5", "L1", 5))
        self.assertEqual(self.codex.request_for({**base, "hook_event_name": "SessionEnd"}),
                         {"cmd": "remove", "session_id": "cx1"})
        self.assertIsNone(self.codex.request_for({**base, "hook_event_name": "PreCompact"}))
        self.assertEqual(self.codex.message({}), "Codex needs your approval")

    def test_hooks_install_into_hooks_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "hooks.json")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["hooks", "install", "--agent", "codex", "--settings", target,
                                       "--command", "/opt/omaorchestra hook codex"]), 0)
                self.assertEqual(main(["hooks", "status", "--agent", "codex", "--settings", target]), 0)
            data = json.loads(Path(target).read_text())
            self.assertEqual(set(data["hooks"]), set(self.codex.events))
            self.assertIn("/hooks", out.getvalue(), "reminds the user to trust the hooks")


class OpenCodeTest(unittest.TestCase):
    oc = adapters.OpenCode()

    def test_command(self):
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_OPENCODE": "opencode"}):
            self.assertEqual(self.oc.command("fix it", "S", "/w", model="opencode/x"),
                             ["opencode", "/w", "--prompt", "fix it", "-m", "opencode/x"])

    def test_uses_the_pid_the_plugin_sends(self):
        self.assertEqual(self.oc.agent_process({"pid": os.getpid()})[0], os.getpid())

    def test_plugin_install_status_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plugins" / "omaorchestra.js"
            self.assertTrue(self.oc.install_hooks("/opt/omaorchestra hook opencode", target))
            text = target.read_text()
            self.assertIn('["/opt/omaorchestra", "hook", "opencode"]', text)
            self.assertIn("permission.asked", text)
            self.assertIsNone(self.oc.install_hooks("/opt/omaorchestra hook opencode", target), "already current")
            self.assertTrue(self.oc.hooks_status(target)[0])
            self.oc.uninstall_hooks(target)
            self.assertFalse(target.exists())
            target.write_text("// someone else's plugin")
            with self.assertRaises(Exception):
                self.oc.install_hooks("x hook opencode", target)
            self.assertIsNone(self.oc.uninstall_hooks(target))
            self.assertTrue(target.exists(), "a foreign plugin is left alone")


class FindByEnvironmentTest(unittest.TestCase):
    def make(self, root, pid, comm, ppid, env):
        d = Path(root, str(pid))
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} " + " ".join(["0"] * 17) + f" {pid * 10} 0 0\n")
        (d / "environ").write_bytes(b"\0".join(e.encode() for e in env) + b"\0")

    def test_the_agent_not_its_terminal_or_children(self):
        with tempfile.TemporaryDirectory() as root:
            tag = "OMAORCHESTRA_LAUNCH_ID=L1"
            self.make(root, 10, "foot", 1, [tag, "HOME=/h"])
            self.make(root, 11, "codex", 10, [tag])
            self.make(root, 12, "bash", 11, [tag])       # a tool the agent runs
            self.make(root, 20, "codex", 1, ["OMAORCHESTRA_LAUNCH_ID=OTHER"])
            self.assertEqual(procs.find_env_process("OMAORCHESTRA_LAUNCH_ID", "L1", ("codex",), root), (11, 110))
            # No process named like the agent (a stand-in script): the terminal's child.
            self.assertEqual(procs.find_env_process("OMAORCHESTRA_LAUNCH_ID", "L1", ("nope",), root)[0], 11)
            self.assertIsNone(procs.find_env_process("OMAORCHESTRA_LAUNCH_ID", "L9", ("codex",), root))


class AdoptionTest(unittest.TestCase):
    def test_placeholder_becomes_the_agents_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = daemon.Daemon(Registry(Path(tmp) / "r.json"))
            d.handle({"cmd": "update", "session_id": "L1", "agent": "codex", "status": "working",
                      "launching": True, "task": "fix it", "title": "fix it", "worktree": "/wt"})
            d.handle({"cmd": "update", "session_id": "cx-real", "agent": "codex", "status": "idle",
                      "launch_id": "L1", "pid": 5, "pid_start": 1, "model": "gpt-5"})
            sessions = d.registry.sessions
            self.assertEqual(list(sessions), ["cx-real"])
            s = sessions["cx-real"]
            self.assertEqual((s["task"], s["worktree"], s["model"], s["status"]), ("fix it", "/wt", "gpt-5", "idle"))
            self.assertNotIn("launching", s)
            # A launch id that is not a pending launch changes nothing.
            d.handle({"cmd": "update", "session_id": "other", "agent": "codex", "status": "idle", "launch_id": "cx-real"})
            self.assertIn("cx-real", sessions)


class LaunchOtherAgentsTest(unittest.TestCase):
    def test_codex_launch(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
                "OMAORCHESTRA_CODEX": "true", "OMAORCHESTRA_CONFIG": os.path.join(tmp, "none.toml"),
                "OMAORCHESTRA_STATE_DIR": tmp}):
            sent, spawned = [], []
            result = launch.run("fix it", tmp, agent="codex", worktree=False, model="gpt-5",
                                spawn=lambda cmd, **kw: spawned.append((cmd, kw["env"])), request=sent.append)
            cmd, env = spawned[0]
            self.assertEqual(env["OMAORCHESTRA_LAUNCH_ID"], result["id"])
            self.assertIn("-C", cmd)
            self.assertEqual((sent[0]["agent"], sent[0]["model"]), ("codex", "gpt-5"))
            for kw in ({"provider": "anthropic"}, {"mcp_profile": "none"}):
                with self.assertRaisesRegex(launch.LaunchError, "cannot"):
                    launch.run("x", tmp, agent="codex", worktree=False, spawn=lambda *a, **k: None,
                               request=lambda p: None, **kw)
            with self.assertRaisesRegex(launch.LaunchError, "unknown agent"):
                launch.run("x", tmp, agent="vscode", spawn=lambda *a, **k: None, request=lambda p: None)

    def test_hook_command_reports_the_launch_id(self):
        sent = []
        event = json.dumps({"hook_event_name": "Stop", "session_id": "cx1", "cwd": "/w"})
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_LAUNCH_ID": "L7", "OMAORCHESTRA_CONFIG": "/nonexistent"}), \
                mock.patch("omaorchestra.client.request", lambda p, timeout=1: sent.append(p)), \
                mock.patch("sys.stdin", io.StringIO(event)):
            main(["hook", "codex"])
        self.assertEqual((sent[0]["launch_id"], sent[0]["agent"]), ("L7", "codex"))


if __name__ == "__main__":
    unittest.main()
