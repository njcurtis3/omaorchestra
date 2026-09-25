import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __main__ as cli, adapters, config, daemon, history, launch
from omaorchestra.registry import Registry

NOW = 1_790_000_000


def session(status="idle", **extra):
    return {"id": "s1", "agent": "claude", "status": status, "cwd": "/w/site", "started": NOW - 600,
            "history": [{"status": "working", "at": NOW - 600}, {"status": "needs-input", "at": NOW - 300},
                        {"status": "working", "at": NOW - 240}, {"status": status, "at": NOW - 60}], **extra}


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def repo(folder):
    git(folder, "init", "-q", "-b", "main")
    (Path(folder) / "a.txt").write_text("one\n")
    git(folder, "add", "a.txt")
    git(folder, "commit", "-q", "-m", "first")
    return history.head(folder)


class OutcomeTest(unittest.TestCase):
    def test_outcomes(self):
        cases = [
            (session("idle"), "session-end", "finished"),
            (session("working"), "session-end", "stopped"),
            (session("idle"), "process-gone", "finished"),
            (session("working"), "process-gone", "crashed"),
            (session("needs-input"), "process-gone", "crashed"),
            (session("working", stopping=True), "process-gone", "stopped"),
            (session("working", launching=True), "did-not-start", "never-started"),
            (session("idle"), "dismissed", "finished"),
            (session("working"), "dismissed", "dismissed"),
        ]
        for s, reason, expected in cases:
            self.assertEqual(history.outcome(s, reason), expected, (s["status"], reason))

    def test_time_per_status_and_waits(self):
        record = history.build(session("idle"), "session-end", ended=NOW, git_fn=lambda *a: None)
        self.assertEqual(record["seconds"], {"working": 480.0, "needs-input": 60.0, "idle": 60.0})
        self.assertEqual((record["waits"], record["outcome"], record["project"]), (1, "finished", "site"))
        self.assertEqual(history.worked(record), 480.0)
        self.assertEqual(history.length(record), 600)


class BuildTest(unittest.TestCase):
    def test_titles_can_be_left_out(self):
        s = session(title="Fix it", task="Fix the login bug properly")
        self.assertEqual(history.build(s, "session-end", git_fn=lambda *a: None)["task"], "Fix the login bug properly")
        kept = history.build(s, "session-end", git_fn=lambda *a: None, titles=False)
        self.assertNotIn("task", kept)
        self.assertNotIn("title", kept)

    def test_cost_is_kept_and_its_failures_ignored(self):
        s = session(transcript_path="/t.jsonl")
        record = history.build(s, "session-end", git_fn=lambda *a: None,
                               cost_fn=lambda _: {"usd": 1.23456, "real": False})
        self.assertEqual(record["cost"], {"usd": 1.2346, "real": False})

        def broken(_):
            raise OSError("gone")
        self.assertIsNone(history.build(s, "session-end", git_fn=lambda *a: None, cost_fn=broken)["cost"])

    def test_commits_made_in_a_real_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            start = repo(folder)
            for n in (2, 3):
                (Path(folder) / "a.txt").write_text(f"{n}\n")
                git(folder, "commit", "-q", "-am", f"change {n}")
            record = history.build(session(cwd=folder, git_start=start), "session-end")
            self.assertEqual(record["git"]["commits"], 2)
            self.assertEqual(record["git"]["from"], start)
            changes = history.changes(record)
            self.assertEqual([c.split(" ", 1)[1] for c in changes["commits"]], ["change 3", "change 2"])
            self.assertIn("+3", changes["diff"])
            self.assertEqual(changes["error"], "")

    def test_no_repository_no_range(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(history.head(folder))
            self.assertIsNone(history.build(session(cwd=folder), "session-end")["git"])


class FileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *records):
        for r in records:
            history.append(r, self.path)

    def test_load_filter_find(self):
        a = {"id": "aaa111", "project": "site", "cwd": "/w/site", "agent": "claude", "ended": NOW - 86400 * 3,
             "outcome": "finished", "title": "Fix the login bug"}
        b = {"id": "bbb222", "project": "api", "cwd": "/w/api", "agent": "codex", "ended": NOW, "outcome": "crashed"}
        self.write(a, b)
        with open(self.path, "a") as f:
            f.write("not json\n")
        records = history.load(self.path)
        self.assertEqual([r["id"] for r in records], ["aaa111", "bbb222"])
        pick = lambda **kw: [r["id"] for r in records if history.matches(r, **kw)]
        self.assertEqual(pick(project="SITE"), ["aaa111"])
        self.assertEqual(pick(agent="codex"), ["bbb222"])
        self.assertEqual(pick(search="login"), ["aaa111"])
        self.assertEqual(pick(outcome="crashed"), ["bbb222"])
        self.assertEqual(pick(since=NOW - 86400), ["bbb222"])
        self.assertEqual(history.find("bbb", records)["project"], "api")
        with self.assertRaises(history.HistoryError):
            history.find("zzz", records)

    def test_since(self):
        self.assertEqual(history.since_seconds("7d", now=NOW), NOW - 7 * 86400)
        self.assertEqual(history.since_seconds("12h", now=NOW), NOW - 12 * 3600)
        self.assertLess(history.since_seconds("2026-09-01"), NOW)
        with self.assertRaises(history.HistoryError):
            history.since_seconds("yesterday")

    def test_prune_and_forget_titles(self):
        self.write({"id": "old", "ended": NOW - 100 * 86400, "title": "x"},
                   {"id": "new", "ended": NOW - 86400, "title": "Secret plan", "task": "Secret plan in full"})
        self.assertEqual(history.prune(90, now=NOW, target=self.path), 1)
        self.assertEqual(history.prune(0, now=NOW, target=self.path), 0)
        self.assertEqual([r["id"] for r in history.load(self.path)], ["new"])
        history.forget_titles(self.path)
        self.assertNotIn("Secret", self.path.read_text())

    def test_stats(self):
        records = [
            {"id": "1", "project": "site", "agent": "claude", "model": "claude-opus-5-5", "outcome": "finished",
             "started": 0, "ended": 600, "seconds": {"working": 500}, "cost": {"usd": 1.5, "real": False}, "waits": 2},
            {"id": "2", "project": "site", "agent": "claude", "model": "claude-opus-5-5", "outcome": "crashed",
             "started": 0, "ended": 100, "seconds": {"working": 100}, "cost": None, "waits": 0},
            {"id": "3", "project": "api", "agent": "codex", "model": None, "outcome": "finished",
             "started": 0, "ended": 60, "seconds": {"working": 30}, "cost": {"usd": 0.5, "real": True}, "waits": 1},
        ]
        data = history.stats(records, answered=[{"waited": 30}, {"waited": 5}, {"waited": 300}])
        self.assertEqual(data["outcomes"]["finished"], 2)
        site = data["by_project"][0]
        self.assertEqual((site["name"], site["sessions"], site["working"], site["usd"], site["estimated"]),
                         ("site", 2, 600, 1.5, True))
        self.assertEqual(data["by_model"][1]["name"], "(unknown)")
        self.assertEqual(data["waits"], {"total": 3, "per_session": 1.0, "answered": 3, "median_seconds": 30,
                                         "longest_seconds": 300})


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "elsewhere"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), is_alive=lambda p, s: False)
        self.d.git_head = lambda cwd: "abc123" if cwd == "/w/repo" else None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def records(self):
        return history.load(Path(self.tmp.name) / "history.jsonl")

    def update(self, sid, status, **extra):
        self.d.handle({"cmd": "update", "session_id": sid, "agent": "claude", "status": status, **extra})

    def test_an_ended_session_is_recorded_beside_the_registry(self):
        self.update("s1", "working", cwd="/w/repo")
        self.assertEqual(self.d.registry.sessions["s1"]["git_start"], "abc123")
        self.update("s1", "idle")
        self.d.handle({"cmd": "remove", "session_id": "s1"})
        (record,) = self.records()
        self.assertEqual((record["id"], record["outcome"], record["reason"]), ("s1", "finished", "session-end"))
        self.assertFalse(Path(self.tmp.name, "elsewhere", "history.jsonl").exists(),
                         "history lives beside this daemon's registry")

    def test_stopped_on_purpose(self):
        self.update("s1", "working", pid=1, pid_start=1)
        self.assertTrue(self.d.handle({"cmd": "stopping", "session_id": "s1"})["known"])
        self.d.prune()  # its process is gone
        self.assertEqual(self.records()[0]["outcome"], "stopped")

    def test_crashed(self):
        self.update("s1", "working", pid=1, pid_start=1)
        self.d.prune()
        self.assertEqual((self.records()[0]["outcome"], self.records()[0]["reason"]), ("crashed", "process-gone"))

    def test_an_adopted_placeholder_is_not_an_ended_session(self):
        self.update("launch-1", "working", launching=True)
        self.update("codex-1", "working", launch_id="launch-1")
        self.assertEqual(self.records(), [])

    def test_retention_follows_the_settings(self):
        path = Path(self.tmp.name) / "history.jsonl"
        history.append({"id": "old", "ended": time.time() - 200 * 86400}, path)
        history.append({"id": "new", "ended": time.time(), "title": "Secret"}, path)
        self.d.settings["history"]["titles"] = False
        self.d.tend_history()
        self.assertEqual([r["id"] for r in self.records()], ["new"])
        self.assertNotIn("Secret", path.read_text())


class ResumeTest(unittest.TestCase):
    RECORD = {"id": "abc-123", "agent": "claude", "cwd": None, "title": "Fix it", "task": "Fix it well",
              "model": "claude-opus-5-5", "ended": NOW}

    def test_commands(self):
        self.assertEqual(adapters.get("claude").resume_command("x", "/w", "claude"), ["claude", "--resume", "x"])
        self.assertEqual(adapters.get("codex").resume_command("x", "/w", "codex"), ["codex", "resume", "-C", "/w", "x"])
        self.assertEqual(adapters.get("opencode").resume_command("x", "/w", "opencode"),
                         ["opencode", "/w", "--session", "x"])

    def test_reopens_in_its_folder_and_registers_it(self):
        spawned, sent = [], []
        with tempfile.TemporaryDirectory() as folder, mock.patch("shutil.which", return_value="/usr/bin/claude"):
            result = launch.resume({**self.RECORD, "cwd": folder}, spawn=lambda cmd, **kw: spawned.append((cmd, kw)),
                                   request=lambda payload: sent.append(payload))
        self.assertEqual(result["id"], "abc-123")
        command, kw = spawned[0]
        self.assertEqual(command[-3:], ["claude", "--resume", "abc-123"])
        self.assertEqual(kw["env"][launch.LAUNCH_VARIABLE], "abc-123")
        self.assertEqual((sent[0]["session_id"], sent[0]["resumed_from"], sent[0]["launching"]), ("abc-123", NOW, True))

    def test_a_gone_folder(self):
        with self.assertRaises(launch.LaunchError):
            launch.resume({**self.RECORD, "cwd": "/no/such/folder"}, spawn=lambda *a, **k: None,
                          request=lambda p: None)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_SOCKET": os.path.join(self.tmp.name, "none.sock")})
        self.env.start()
        history.append({"id": "aaa11111-x", "project": "site", "cwd": "/w/site", "agent": "claude",
                        "started": NOW - 700, "ended": NOW - 100, "outcome": "finished", "reason": "session-end",
                        "seconds": {"working": 500}, "cost": {"usd": 0.42, "real": False}, "waits": 1,
                        "git": {"from": "a" * 40, "to": "b" * 40, "commits": 3}, "title": "Fix the login bug"})
        history.append({"id": "bbb22222-y", "project": "api", "cwd": "/w/api", "agent": "codex",
                        "started": NOW - 50, "ended": NOW, "outcome": "crashed", "reason": "process-gone",
                        "seconds": {"working": 50}, "waits": 0})

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", io.StringIO()) as err:
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_list_and_filters(self):
        _, out, _ = self.run_cli("history")
        self.assertLess(out.index("api"), out.index("site"), "newest first")
        self.assertIn("~$0.42", out)
        self.assertIn("3 commits", out)
        self.assertIn("Fix the login bug", out)
        _, out, _ = self.run_cli("history", "--outcome", "crashed")
        self.assertNotIn("site", out)
        _, out, _ = self.run_cli("history", "--search", "nothing-like-this")
        self.assertIn("no sessions in the history match", out)
        code, out, _ = self.run_cli("history", "--json", "--agent", "codex")
        self.assertEqual([r["id"] for r in json.loads(out)], ["bbb22222-y"])

    def test_show_and_errors(self):
        code, out, _ = self.run_cli("history", "show", "aaa")
        self.assertEqual(code, 0)
        self.assertIn("finished (session-end)", out)
        self.assertIn("omaorchestra resume aaa11111", out)
        code, _, err = self.run_cli("history", "show", "zzz")
        self.assertEqual((code, err.strip()), (1, "omaorchestra: no session zzz in the history"))

    def test_stats(self):
        code, out, _ = self.run_cli("history", "stats")
        self.assertIn("2 session(s): 1 finished, 1 crashed", out)
        self.assertIn("By project", out)

    def test_resume(self):
        with mock.patch.object(launch, "resume", return_value={"id": "aaa11111-x", "tracked": True,
                                                                "folder": "/w/site"}) as resume:
            code, out, _ = self.run_cli("resume", "aaa")
        self.assertEqual(resume.call_args[0][0]["id"], "aaa11111-x")
        self.assertIn("resumed aaa11111 (claude) in /w/site", out)


if __name__ == "__main__":
    unittest.main()
