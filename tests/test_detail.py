import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import changes, control, transcript
from omaorchestra.registry import Registry


def jsonl(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


class ActivityTest(unittest.TestCase):
    def test_prompts_replies_and_tools_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.jsonl"
            jsonl(p, [
                {"type": "ai-title", "aiTitle": "Old title"},
                {"type": "user", "timestamp": "T1", "message": {"content": "fix   the\nbug"}},
                {"type": "assistant", "timestamp": "T2", "message": {"content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "Looking."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/a.py", "old_string": "x"}},
                    {"type": "tool_use", "name": "Mystery", "input": {"n": 1}},
                ]}},
                {"type": "user", "timestamp": "T3", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
                {"type": "user", "timestamp": "T4", "message": {"content": "<command-name>/usage</command-name>"}},
                {"type": "user", "timestamp": "T5", "isMeta": True, "message": {"content": "meta"}},
                {"type": "assistant", "timestamp": "T6", "isSidechain": True, "message": {"content": [{"type": "text", "text": "subagent"}]}},
                {"type": "user", "timestamp": "T7", "message": {"content": [{"type": "text", "text": "thanks"}]}},
                {"type": "ai-title", "aiTitle": "Bug fix"},
            ])
            items = transcript.activity(p)
            info = transcript.info(p)
        self.assertEqual([(i["at"], i["kind"], i["text"]) for i in items], [
            ("T1", "prompt", "fix the bug"),
            ("T2", "reply", "Looking."),
            ("T2", "tool", "Bash: pytest -q"),
            ("T2", "tool", "Edit: /w/a.py"),
            ("T2", "tool", "Mystery"),
            ("T7", "prompt", "thanks"),
        ])
        self.assertEqual(info["title"], "Bug fix")

    def test_limit_and_long_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.jsonl"
            jsonl(p, [{"type": "user", "message": {"content": f"prompt {i} " + "x" * 400}} for i in range(10)])
            items = transcript.activity(p, limit=3)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0]["text"].startswith("prompt 7"))
        self.assertLessEqual(len(items[0]["text"]), 300)
        self.assertTrue(items[0]["text"].endswith("…"))

    def test_missing_transcript(self):
        self.assertEqual(transcript.activity("/nonexistent.jsonl"), [])


class HistoryTest(unittest.TestCase):
    def test_history_records_status_changes_only_and_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Registry(Path(tmp) / "r.json")
            r.HISTORY_LIMIT = 3
            for status in ("idle", "working", "working", "needs-input", "working", "idle"):
                s = r.update("s", "claude", status)
        self.assertEqual([h["status"] for h in s["history"]], ["needs-input", "working", "idle"])


class ChangesTest(unittest.TestCase):
    def git(self, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    def test_not_a_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(changes.uncommitted(tmp)["repo"])
        self.assertEqual(changes.uncommitted("")["error"], "the session has no folder")

    def test_diff_stat_untracked_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.email", "t@example.com")
            self.git(repo, "config", "user.name", "t")
            (repo / "a.txt").write_text("one\n")
            self.git(repo, "add", "a.txt")
            self.git(repo, "commit", "-qm", "first")
            (repo / "a.txt").write_text("one\ntwo\n" + "long line\n" * 200)
            (repo / "new.txt").write_text("new\n")
            full = changes.uncommitted(repo)
            cut = changes.uncommitted(repo, max_bytes=100)
        self.assertTrue(full["repo"])
        self.assertIn("a.txt", full["stat"])
        self.assertIn("+two", full["diff"])
        self.assertEqual(full["untracked"], ["new.txt"])
        self.assertFalse(full["truncated"])
        self.assertTrue(cut["truncated"])
        self.assertLessEqual(len(cut["diff"].encode()), 100)

    def test_repository_without_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.git(repo, "init", "-q")
            (repo / "a.txt").write_text("x\n")
            self.git(repo, "add", "a.txt")
            result = changes.uncommitted(repo)
        self.assertTrue(result["repo"])
        self.assertIn("+x", result["diff"])


class StopTest(unittest.TestCase):
    SESSION = {"id": "abcdef123", "pid": 42, "pid_start": 7}

    def test_signals_only_the_recorded_process(self):
        sent = []
        control.stop(self.SESSION, is_alive=lambda pid, st: (pid, st) == (42, 7), kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [(42, signal.SIGTERM)])

    def test_refuses_reused_or_missing_process(self):
        for session, alive in ((self.SESSION, False), ({"id": "x"}, True)):
            with self.assertRaises(control.ControlError):
                control.stop(session, is_alive=lambda pid, st: alive, kill=lambda pid, sig: self.fail("signalled"))

    def test_stops_a_real_process(self):
        from omaorchestra import procs
        proc = subprocess.Popen(["sleep", "30"])
        try:
            control.stop({"id": "real", "pid": proc.pid, "pid_start": procs.start_time(proc.pid)})
            self.assertEqual(proc.wait(timeout=5), -signal.SIGTERM)
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
