import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, handoff
from omaorchestra.registry import Registry


class BriefTest(unittest.TestCase):
    def test_brief_has_task_git_and_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "-C", tmp, "init", "-q", "-b", "fix-login"], check=True)
            (repo / "a.txt").write_text("x\n")
            t = repo / "t.jsonl"
            t.write_text(json.dumps({"type": "user", "timestamp": "T", "message": {"content": "fix the login bug"}}) + "\n"
                         + json.dumps({"type": "assistant", "timestamp": "T", "message": {"content": [
                             {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/login"}}]}}) + "\n")
            text = handoff.brief({"id": "s1", "agent": "claude", "cwd": tmp, "task": "Fix the login bug",
                                  "transcript_path": str(t)})
        self.assertIn("taking over work that Claude Code was doing", text)
        self.assertIn("The task was:\nFix the login bug", text)
        self.assertIn("Git: on branch fix-login", text)
        self.assertIn("?? a.txt", text)
        self.assertIn("- tool: Bash: pytest tests/login", text)

    def test_needs_a_folder(self):
        with self.assertRaises(ValueError):
            handoff.workdir({"id": "s1", "cwd": "/no/such/dir"})


class DaemonHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml"),
                                                "OMAORCHESTRA_CODEX": "true", "OMAORCHESTRA_CLAUDE": "true"})
        self.env.start()
        settings = daemon.config.defaults()
        settings["tasks"]["max_parallel"] = 10
        settings["tasks"]["fallback_agent"] = "codex"
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        self.spawned = []
        self.d.spawn = lambda cmd, **kw: self.spawned.append(cmd)
        self.limits = set()
        self.d.usage_check = lambda agent, threshold: (
            {"agent": agent, "name": agent, "label": "Session", "percent": 1.0, "resetsAt": None}
            if agent in self.limits else None)
        self.d.usage_refresh = lambda agent: None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_handoff_starts_the_fallback_with_a_brief(self):
        self.d.handle({"cmd": "update", "session_id": "s1", "agent": "claude", "status": "working",
                       "cwd": self.tmp.name, "task": "write the docs", "path": "/usr/bin:/bin"})
        self.assertNotIn("path", self.d.registry.sessions["s1"], "PATH is kept in memory, not in the registry")
        response = self.d.handle({"cmd": "handoff", "session_id": "s1"})
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["agent"], "codex")
        cmd = self.spawned[-1]
        self.assertIn("-C", cmd)
        self.assertIn("write the docs", cmd[-1])
        self.assertEqual(self.d.registry.sessions[response["session_id"]]["agent"], "codex")
        self.assertFalse(self.d.handle({"cmd": "handoff", "session_id": "nope"})["ok"])

    def test_queue_falls_back_when_the_agent_is_at_its_limit(self):
        self.limits = {"claude"}
        self.d.handle({"cmd": "queue-add", "item": {"task": "t", "cwd": self.tmp.name, "worktree": False,
                                                    "path": "/usr/bin:/bin"}})
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.spawned[0][self.spawned[0].index("-e") + 1], "true")  # codex's stand-in
        (session,) = self.d.registry.sessions.values()
        self.assertEqual(session["agent"], "codex")
        self.limits = {"claude", "codex"}
        self.d.handle({"cmd": "queue-add", "item": {"task": "u", "cwd": self.tmp.name, "worktree": False}})
        self.assertEqual(len(self.spawned), 1, "no fallback when the fallback is at its limit too")

    def test_offers_a_handoff_once_per_session(self):
        offers = []

        class Notifier:
            settings = {}

            def changed(self, *a):
                pass

            def offer(self, summary, body, label, on_accept):
                offers.append((summary, label))

        self.d.notifier = Notifier()
        self.d.handle({"cmd": "update", "session_id": "s1", "agent": "claude", "status": "working", "cwd": "/w/app"})
        self.d.handle({"cmd": "update", "session_id": "s2", "agent": "claude", "status": "idle", "cwd": "/w/b"})
        self.d.offer_handoffs()
        self.assertEqual(offers, [])
        self.limits = {"claude"}
        self.d.offer_handoffs()
        self.d.offer_handoffs()
        self.assertEqual(offers, [("app: claude's Session limit is at 100%", "Hand off to Codex")])


if __name__ == "__main__":
    unittest.main()
