import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import client, daemon, launch
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry


class CommandTest(unittest.TestCase):
    def test_claude_command(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OMAORCHESTRA_CLAUDE", None)
            self.assertEqual(launch.claude_command("fix it", "S"), ["claude", "--session-id", "S", "--", "fix it"])
            self.assertEqual(
                launch.claude_command("-x looks like a flag", "S", permission_mode="auto", model="haiku", extra=["--verbose"]),
                ["claude", "--session-id", "S", "--permission-mode", "auto", "--model", "haiku", "--verbose",
                 "--", "-x looks like a flag"])

    def test_terminal_command(self):
        self.assertEqual(launch.terminal_command("/w", ["claude", "x"]),
                         ["setsid", "uwsm-app", "--", "xdg-terminal-exec", "--app-id=org.omarchy.agent",
                          "--dir=/w", "-e", "claude", "x"])


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_CLAUDE": "true"})  # any installed binary
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_registers_then_launches(self):
        sent, spawned = [], []
        sid, tracked = launch.run("  fix the   flaky test  ", self.tmp.name,
                                  spawn=lambda cmd, **kw: spawned.append(cmd), request=sent.append)
        self.assertTrue(tracked)
        reg = sent[0]
        self.assertEqual((reg["session_id"], reg["status"], reg["launching"], reg["title"], reg["cwd"]),
                         (sid, "working", True, "fix the flaky test", str(Path(self.tmp.name).resolve())))
        self.assertIn("--session-id", spawned[0])
        self.assertEqual(spawned[0][spawned[0].index("--session-id") + 1], sid)
        self.assertEqual(spawned[0][-1], "  fix the   flaky test  ")

    def test_untracked_when_daemon_is_down(self):
        def down(payload):
            raise client.DaemonUnavailable("no")
        sid, tracked = launch.run("x", self.tmp.name, spawn=lambda cmd, **kw: None, request=down)
        self.assertFalse(tracked)

    def test_failed_terminal_unregisters(self):
        sent = []

        def broken(cmd, **kw):
            raise OSError("no terminal")
        with self.assertRaises(launch.LaunchError):
            launch.run("x", self.tmp.name, spawn=broken, request=sent.append)
        self.assertEqual(sent[-1]["cmd"], "remove")
        self.assertEqual(sent[-1]["reason"], "did-not-start")

    def test_refusals(self):
        for task, where in (("  ", self.tmp.name), ("x", "/nonexistent-dir")):
            with self.assertRaises(launch.LaunchError):
                launch.run(task, where, spawn=lambda cmd, **kw: None, request=lambda p: None)
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_CLAUDE": "no-such-agent-binary"}):
            with self.assertRaises(launch.LaunchError):
                launch.run("x", self.tmp.name, spawn=lambda cmd, **kw: None, request=lambda p: None)

    def test_cli_passes_agent_args_after_dashes(self):
        captured = {}

        def fake_run(task, cwd, **kw):
            captured.update(task=task, cwd=cwd, **kw)
            return "abcdef1234", True
        out = io.StringIO()
        with mock.patch.object(launch, "run", fake_run), contextlib.redirect_stdout(out):
            self.assertEqual(main(["run", "do it", "--in", self.tmp.name, "--model", "haiku", "--", "--verbose"]), 0)
        self.assertEqual((captured["task"], captured["model"], captured["extra"]), ("do it", "haiku", ["--verbose"]))
        self.assertIn("started abcdef12", out.getvalue())


class LaunchingStateTest(unittest.TestCase):
    def test_claimed_by_the_agent_and_unclaimed_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Registry(Path(tmp) / "r.json")
            d = daemon.Daemon(r, is_alive=lambda pid, st: True)
            d.handle({"cmd": "update", "session_id": "claimed", "agent": "claude", "status": "working", "launching": True})
            d.handle({"cmd": "update", "session_id": "stale", "agent": "claude", "status": "working", "launching": True})
            self.assertTrue(r.sessions["claimed"]["launching"])
            # The agent's first hook reports its process: no longer launching.
            d.handle({"cmd": "update", "session_id": "claimed", "agent": "claude", "status": "idle", "pid": 5, "pid_start": 1})
            self.assertNotIn("launching", r.sessions["claimed"])
            r.sessions["stale"]["started"] -= r.LAUNCH_TIMEOUT + 1
            with self.assertLogs("omaorchestra", level="INFO") as logs:
                removed = d.prune()
        self.assertEqual(removed, ["stale"])
        self.assertIn("reason=did-not-start", logs.output[0])


class FindProcessTest(unittest.TestCase):
    def make(self, root, pid, comm, ppid, argv, start=100):
        d = Path(root, str(pid))
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} " + " ".join(["0"] * 17) + f" {start} 0 0\n")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def test_takes_the_agent_not_its_terminal(self):
        from omaorchestra import procs
        with tempfile.TemporaryDirectory() as root:
            self.make(root, 10, "foot", 1, ["foot", "--app-id=x", "-e", "claude", "--session-id", "S1", "--", "task"])
            self.make(root, 11, "claude", 10, ["claude", "--session-id", "S1", "--", "task"], start=222)
            self.make(root, 12, "bash", 11, ["bash", "-c", "ls"])
            self.make(root, 20, "claude", 1, ["claude", "--session-id", "OTHER"])
            self.assertEqual(procs.find_session_process("S1", root), (11, 222))
            self.assertIsNone(procs.find_session_process("NOPE", root))

    def test_script_stand_in(self):
        from omaorchestra import procs
        with tempfile.TemporaryDirectory() as root:
            self.make(root, 10, "foot", 1, ["foot", "-e", "/t/claude", "--session-id", "S1"])
            self.make(root, 11, "claude", 10, ["/bin/bash", "/t/claude", "--session-id", "S1"])
            self.assertEqual(procs.find_session_process("S1", root)[0], 11)


class ClaimLaunchesTest(unittest.TestCase):
    def test_found_then_waiting_then_claimed_by_its_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Registry(Path(tmp) / "r.json")
            changes = []

            class Recorder:
                def changed(self, previous, session):
                    changes.append((previous and previous.get("status"), session and session.get("status")))

            d = daemon.Daemon(r, is_alive=lambda pid, st: True, notifier=Recorder())
            d.handle({"cmd": "update", "session_id": "L", "agent": "claude", "status": "working", "launching": True})
            started = r.sessions["L"]["started"]

            d.claim_launches(now=started + 1, find=lambda sid: None)
            self.assertNotIn("pid", r.sessions["L"])
            d.claim_launches(now=started + 2, find=lambda sid: (77, 700))
            self.assertEqual((r.sessions["L"]["pid"], r.sessions["L"]["launching"]), (77, True))
            d.claim_launches(now=started + 5, find=lambda sid: self.fail("already found"))
            self.assertEqual(r.sessions["L"]["status"], "working")
            d.claim_launches(now=started + daemon.LAUNCH_QUIET_SECONDS + 1)
            self.assertEqual((r.sessions["L"]["status"], r.sessions["L"]["message"]),
                             ("needs-input", daemon.NOT_STARTED_MESSAGE))
            # Its process is known, so it is not dropped as did-not-start.
            r.sessions["L"]["started"] -= r.LAUNCH_TIMEOUT + 1
            self.assertEqual(d.prune(), [])
            # The first hook (trust answered) claims it for good.
            d.handle({"cmd": "update", "session_id": "L", "agent": "claude", "status": "idle", "pid": 77, "pid_start": 700})
            self.assertNotIn("launching", r.sessions["L"])
        self.assertEqual(changes[-1], ("needs-input", "idle"))
        self.assertIn(("working", "needs-input"), changes)


if __name__ == "__main__":
    unittest.main()
