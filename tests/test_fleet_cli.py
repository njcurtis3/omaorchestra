import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import client, daemon, fleet, fleet_graph, fleet_reply
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def plan_reply(n=2):
    slices = [{"id": f"s{i}", "intent": f"Part {i}", "files": [f"part{i}.py"], "done_when": "true -> passes",
               "risk": "low", "risk_why": "small"} for i in range(1, n + 1)]
    return example("architect", shape="single-loop", slices=slices, edges=[], not_doing=["The docs"],
                   approve="Two parts, one after the other")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def commit(cwd, name):
    (Path(cwd) / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-qm", name)


class FleetCliTest(unittest.TestCase):
    """`omaorchestra fleet ...` against a real daemon object (in place of the socket)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        commit(self.repo, "a.py")
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml"),
                                                "OMAORCHESTRA_WORKTREES": str(self.root / "wt"),
                                                "OMAORCHESTRA_CLAUDE": "true", **GIT_ENV})
        self.env.start()
        self.d = daemon.Daemon(Registry(self.root / "state" / "sessions.json"), is_alive=lambda p, s: True)
        self.d.spawn = lambda cmd, **kw: None
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None
        self.requests = []

        def request(payload, timeout=None):
            self.requests.append(payload)
            return self.d.handle(json.loads(json.dumps(payload)))
        self.patch = mock.patch.object(client, "request", request)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue() + err.getvalue()

    def finish(self, nid, reply, work=None):
        state = fleet.load(self.run)
        session = self.d.registry.sessions[state["nodes"][nid]["session"]]
        if work:
            work(session["cwd"])
        transcript = self.root / f"{session['id']}.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": reply if isinstance(reply, str) else block(reply)}]}}) + "\n")
        for status in ("working", "idle"):
            self.d.handle({"cmd": "update", "session_id": session["id"], "agent": "claude", "status": status,
                           "cwd": session["cwd"], "transcript_path": str(transcript)})

    def start(self, *extra):
        code, out = self.cli("fleet", "run", "Make it better", "--in", str(self.repo), *extra)
        self.assertEqual(code, 0, out)
        self.run = self.requests[-1] and fleet.runs()[0]["id"]
        return out

    def test_a_run_from_start_to_close(self):
        out = self.start("--budget", "7.5")
        self.assertIn(f"started {self.run} (auto fleet)", out)
        self.assertIn("scout first", out)
        self.assertEqual(self.requests[-1]["path"], os.environ["PATH"])
        self.assertEqual(fleet.load(self.run)["limits"]["budget"], 7.5)
        self.finish("scout", example("scout"))
        self.finish("architect", plan_reply())
        code, out = self.cli("fleet", "show", self.run[:14])
        for expected in ("waiting at the plan gate", "s1: Part 1", "files: part1.py", "done when: true -> passes",
                         "not doing: The docs", "to approve: Two parts", f"omaorchestra fleet approve {self.run}"):
            self.assertIn(expected, out)
        code, out = self.cli("fleet", "list")
        self.assertIn(self.run, out)
        self.assertIn("waiting at the plan gate", out)
        self.assertEqual(self.cli("fleet", "approve", self.run, "--note", "go"), (0, "approved; the run goes on\n"))
        self.finish("builder.s1", example("builder"), lambda cwd: commit(cwd, "part1.py"))
        code, out = self.cli("fleet", "show", self.run, "--activity", "3")
        self.assertIn("s1      done         running  1", out)
        self.assertIn("s2      not started", out)
        self.assertIn("started        node=reviewer.s1", out)  # the last events
        self.finish("reviewer.s1", example("reviewer", verdict="PASS", findings=[]))
        self.finish("builder.s2", example("builder"), lambda cwd: commit(cwd, "part2.py"))
        self.finish("reviewer.s2", example("reviewer", verdict="PASS", findings=[]))
        code, out = self.cli("fleet", "close", self.run, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("  ok   the run finished", out)
        self.assertFalse(fleet.load(self.run).get("closed"))
        code, out = self.cli("fleet", "close", self.run)
        self.assertEqual(code, 0, out)
        self.assertIn("closed\n", out)
        self.assertIn("omaorchestra worktree merge", out)
        self.assertIn("closed", self.cli("fleet", "list")[1])

    def test_a_split_at_its_gate_and_on_the_board(self):
        (self.root / "fleets.toml").write_text('[fleets.deep]\nshape = "single-loop"\nmax_depth = 2\n'
                                               'split_gate = true\n')
        self.start("--fleet", "deep")
        self.finish("scout", example("scout"))
        plan = plan_reply(1)
        plan["slices"][0]["files"] = ["pkg/"]
        self.finish("architect", plan)
        self.cli("fleet", "approve", self.run)
        pieces = [{"id": c, "intent": f"Piece {c}", "files": [f"pkg/{c}.py"], "done_when": "true -> passes",
                   "risk": "low", "risk_why": "small"} for c in ("a", "b")]
        self.finish("builder.s1", {"status": "split", "changed": [],
                                   "split": {"rationale": "Two parts, checked apart", "slices": pieces, "edges": []}})
        code, out = self.cli("fleet", "show", self.run)
        for expected in ("waiting at the split gate", "builder.s1 split its slice: Two parts, checked apart",
                         "s1-a: Piece a", "files: pkg/a.py", f"omaorchestra fleet approve {self.run}   (or: cancel)"):
            self.assertIn(expected, out)
        self.assertEqual(self.cli("fleet", "approve", self.run)[0], 0)
        code, out = self.cli("fleet", "show", self.run)
        self.assertNotIn("split its slice", out)
        self.assertIn("  s1      split        -", out)
        self.assertIn("    s1-a  running      -", out)  # a smaller slice, indented under its slice
        self.assertIn("    s1-b  not started", out)

    def test_answers_at_the_plan_gate(self):
        self.start()
        self.finish("scout", example("scout"))
        self.finish("architect", plan_reply(3))
        self.assertEqual(self.cli("fleet", "drop", self.run, "s3"), (0, "dropped s3\n  waiting at the plan gate\n"))
        code, out = self.cli("fleet", "shape", self.run, "diamond")
        self.assertIn("it will run as single-loop (single-loop instead of diamond: 2 slices", out)
        code, out = self.cli("fleet", "send-back", self.run, "One slice is enough")
        self.assertIn("sent back", out)
        self.assertEqual(fleet.load(self.run)["nodes"]["architect.2"]["feedback"], ["One slice is enough"])

    def test_holds_and_their_answers(self):
        self.start("--fleet", "single-loop")
        self.finish("scout", "I looked around, all fine.")  # no JSON block
        code, out = self.cli("fleet", "list")
        self.assertIn("HELD", out)
        self.assertIn("scout: its reply does not end with a ```json block", out)
        code, out = self.cli("fleet", "retry", self.run, "--note", "End with the JSON block")
        self.assertEqual(out, "trying again: scout.2\n")
        state = fleet.load(self.run)
        self.assertEqual((state["status"], state["nodes"]["scout"]["status"]), ("running", "failed"))
        self.assertEqual(state["nodes"]["scout.2"]["status"], "running")
        self.assertIn("End with the JSON block", fleet.brief(state, "scout.2"))
        self.finish("scout.2", example("scout"))
        self.finish("architect", plan_reply(1))
        self.cli("fleet", "approve", self.run)
        self.finish("builder.s1", example("builder"), lambda cwd: (commit(cwd, "part1.py"), commit(cwd, "x.py")))
        code, out = self.cli("fleet", "show", self.run)
        self.assertIn("outside its files: x.py", out)
        code, out = self.cli("fleet", "retry", self.run)
        self.assertIn("extra files", out)
        self.assertEqual(code, 1)
        code, out = self.cli("fleet", "accept-files", self.run, "builder.s1", "x.py is its helper")
        self.assertEqual(code, 0, out)
        self.assertIn("accepted: x.py", self.cli("fleet", "show", self.run)[1])
        code, out = self.cli("fleet", "limits", self.run)
        self.assertEqual(code, 1)
        code, out = self.cli("fleet", "limits", self.run, "--budget", "3", "--steps", "40")
        self.assertIn("budget $3.0 (0: none), 40 steps", out)
        self.assertEqual(self.cli("fleet", "cancel", self.run)[0], 0)
        code, out = self.cli("fleet", "close", self.run)
        self.assertEqual(code, 1)
        self.assertIn("NOT  the run is cancelled, not finished", out)

    def test_other_output(self):
        self.start("--shape", "single-loop")
        self.assertEqual(fleet.load(self.run)["template"]["shape"], "single-loop")
        code, out = self.cli("fleet", "list", "--json")
        self.assertEqual(json.loads(out)[0]["id"], self.run)
        self.assertEqual(json.loads(self.cli("fleet", "show", self.run, "--json")[1])["run"]["id"], self.run)
        Path(os.environ["OMAORCHESTRA_CONFIG"]).parent.joinpath("fleets.toml").write_text(
            '[fleets.careful]\ndescription = "Mine"\nscout = false\nbudget = 2\n[fleets.careful.roles]\n'
            'reviewer = "security-reviewer"\n')
        code, out = self.cli("fleet", "templates")
        self.assertIn("careful        auto        Mine  (yours)", out)
        self.assertIn("no scout; budget $2; reviewer: security-reviewer", out)
        for args, error in ((("fleet", "show", "nope"), "no run matching nope"),
                            (("fleet", "run", "x", "--in", str(self.repo), "--fleet", "nope"), "no fleet nope")):
            code, out = self.cli(*args)
            self.assertEqual(code, 1)
            self.assertIn(error, out)
        with mock.patch.object(client, "request", mock.Mock(side_effect=client.DaemonUnavailable("down"))):
            self.assertIn("omaorchestrad is not running", self.cli("fleet", "list")[1])


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.state = fleet.create("x", self.tmp.name, fleet_graph.get("auto"))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_what_a_retry_starts(self):
        with self.assertRaises(fleet.RunError):
            fleet_graph.retry(self.state)  # not held
        fleet.hold(self.state, "the budget", by="limits")
        with self.assertRaises(fleet.RunError):
            fleet_graph.retry(self.state)
        self.state["held_by"] = "s1"  # a slice rejected too often
        self.state["nodes"]["architect"] = {"id": "architect", "role": "architect", "status": "done", "attempt": 1,
                                            "result": plan_reply(1), "slice": None}
        self.assertEqual(fleet_graph.retry(self.state, "smaller steps"), "builder.s1")
        self.assertEqual(self.state["nodes"]["builder.s1"]["feedback"], ["smaller steps"])
        fleet.hold(self.state, "could not make the run's worktree")  # held on nothing named
        self.assertIsNone(fleet_graph.retry(self.state))
        self.assertEqual(self.state["status"], "running")


if __name__ == "__main__":
    unittest.main()
