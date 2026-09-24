import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, permissions
from omaorchestra.registry import Registry


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir()
        (self.home / ".config" / "opencode").mkdir(parents=True)
        self.project = Path(self.tmp.name) / "proj"
        (self.project / ".claude").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_AGENT_HOME": str(self.home),
                                                "OMAORCHESTRA_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_mcp_server_of(self):
        self.assertEqual(permissions.mcp_server_of("mcp__github__create_issue"), "github")
        self.assertEqual(permissions.mcp_server_of("mcp__db"), "db")
        self.assertIsNone(permissions.mcp_server_of("Bash(npm test:*)"))

    def test_reads_every_level_and_groups_mcp_rules(self):
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {
            "allow": ["Bash(npm test:*)", "mcp__github__list_issues"], "deny": ["mcp__github__delete_repo"],
            "defaultMode": "acceptEdits"}, "deniedMcpServers": ["stripe"]}))
        (self.project / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {
            "ask": ["mcp__db__query", "Edit"]}}))
        (self.home / ".codex" / "config.toml").write_text('approval_policy = "on-request"\nsandbox_mode = "workspace-write"\n')
        (self.home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"permission": {"edit": "ask"}}))
        data = permissions.everything([self.project])
        user, local = data["claude"]
        self.assertEqual((user["source"], user["default_mode"]), ("user", "acceptEdits"))
        self.assertEqual(user["mcp_rules"], {"github": {"allow": ["mcp__github__list_issues"], "ask": [],
                                                        "deny": ["mcp__github__delete_repo"]}})
        self.assertEqual(user["mcp_lists"], {"deniedMcpServers": ["stripe"]})
        self.assertTrue(local["source"].endswith("(local)"))
        self.assertEqual(local["mcp_rules"]["db"]["ask"], ["mcp__db__query"])
        self.assertEqual(data["codex"][0]["approval_policy"], "on-request")
        self.assertEqual(data["opencode"][0]["permission"], {"edit": "ask"})


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_daemon_records_requests_and_how_they_ended(self):
        d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), is_alive=lambda p, s: False)
        up = {"cmd": "update", "session_id": "s1", "agent": "claude", "cwd": "/w/app"}
        d.handle({**up, "status": "working"})
        d.handle({**up, "status": "needs-input", "message": "Allow Bash: rm -rf build?"})
        d.handle({**up, "status": "needs-input", "message": "still waiting"})   # not a new request
        d.handle({**up, "status": "working"})
        d.handle({**up, "status": "needs-input", "message": "Allow Edit?", "pid": 5, "pid_start": 1})
        d.prune()                                                              # the agent died
        record = permissions.record()
        self.assertEqual([(e["message"], e["outcome"]) for e in record],
                         [("Allow Edit?", "session ended (process-gone)"), ("Allow Bash: rm -rf build?", "continued")])
        self.assertIsNotNone(record[1]["waited"])

    def test_open_requests_and_trimming(self):
        permissions.log({"kind": "asked", "session": "a", "project": "/w", "message": "m"})
        self.assertEqual(permissions.record()[0]["outcome"], "waiting")
        for i in range(int(permissions.RECORD_KEPT * 1.3)):
            permissions.log({"kind": "asked", "session": f"s{i}", "message": "x"})
        lines = (Path(self.tmp.name) / "approvals.jsonl").read_text().splitlines()
        self.assertLessEqual(len(lines), permissions.RECORD_KEPT * 1.2 + 1)


if __name__ == "__main__":
    unittest.main()
