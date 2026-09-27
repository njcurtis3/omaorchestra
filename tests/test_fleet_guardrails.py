import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import costs, daemon, fleet, fleet_close, fleet_graph, fleet_reply, fleet_scope, top
from omaorchestra.registry import Registry

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def plan_reply(n=1, shape="single-loop", files=None):
    slices = [{"id": f"s{i}", "intent": f"Part {i}", "files": (files or {}).get(f"s{i}", [f"part{i}.py"]),
               "done_when": "true -> passes", "risk": "low", "risk_why": "small"} for i in range(1, n + 1)]
    return example("architect", shape=shape, slices=slices, edges=[])


PASS = example("reviewer", verdict="PASS", findings=[])


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def commit(cwd, *names):
    for name in names:
        path = Path(cwd) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name}\n")
        git(cwd, "add", name)
    git(cwd, "commit", "-qm", " ".join(names))


class ScopeTest(unittest.TestCase):
    def test_what_a_slice_covers(self):
        for pattern, path, covered in (("a.py", "a.py", True), ("./a.py", "a.py", True), ("a.py", "a.pyc", False),
                                       ("src/", "src/x/y.py", True), ("src", "src/x.py", True),
                                       ("src", "srcx/a.py", False), ("tests/*.py", "tests/test_a.py", True),
                                       ("tests/*.py", "docs/a.py", False)):
            self.assertEqual(fleet_scope.covers(pattern, path), covered, (pattern, path))
        self.assertEqual(fleet_scope.outside(["a.py", "src/b.py", "c.py"], ["a.py", "src"]), ["c.py"])

    def test_changed_files_committed_uncommitted_and_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            commit(tmp, "a.py", "b.py")
            base = git(tmp, "rev-parse", "HEAD")
            commit(tmp, "c.py")
            (Path(tmp) / "a.py").write_text("changed\n")
            (Path(tmp) / "new.py").write_text("new\n")
            self.assertEqual(fleet_scope.changed(tmp, base), ["a.py", "c.py", "new.py"])
            self.assertEqual(fleet_scope.branch_changed(tmp, base, "HEAD"), ["c.py"])
            self.assertIsNone(fleet_scope.changed(tmp, "no-such-commit"))


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def approved(self, plan=None, repo=False, **template):
        state = fleet.create("Make it better", self.tmp.name, {**fleet_graph.get("auto"), **template})
        state["repo"] = repo
        for nid, reply in (("scout", example("scout")), ("architect", plan or plan_reply())):
            fleet_graph.advance(state)
            self.answer(state, nid, reply)
        fleet_graph.advance(state)
        fleet_graph.approve(state, "plan")
        return state

    def answer(self, state, nid, reply, **extra):
        fleet.node_started(state, nid, f"S-{nid}", "claude")
        fleet.record_reply(state, nid, block(reply), **extra)

    def test_template_limits(self):
        path = Path(self.tmp.name) / "fleets.toml"
        path.write_text("[fleets.x]\nbudget = 2.5\nmax_steps = 12\nstall_minutes = 5\n")
        x = fleet_graph.load(path)["x"]
        self.assertEqual((x["budget"], x["max_steps"], x["stall_minutes"]), (2.5, 12, 5))
        self.assertEqual(fleet_graph.get("auto")["max_steps"], 30)
        for text, error in (("budget = -1", "budget"), ("max_steps = 2", "max_steps"), ("stall_minutes = 0", "stall")):
            path.write_text(f"[fleets.x]\n{text}\n")
            with self.subTest(text=text), self.assertRaises(fleet_graph.FleetError) as caught:
                fleet_graph.load(path)
            self.assertIn(error, str(caught.exception))

    def test_the_budget_holds_the_run_until_raised(self):
        state = self.approved(budget=5)
        self.assertEqual(state["limits"], {"budget": 5, "max_steps": 30})
        self.assertEqual(fleet_graph.advance(state), ["builder.s1"])
        self.answer(state, "builder.s1", example("builder"), cost={"usd": 6.25, "real": False})
        self.assertEqual(fleet.spent(state), 6.25)
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual((state["status"], state["held_by"]), ("held", "limits"))
        self.assertEqual(state["reason"], "the run has spent $6.25, at its $5 budget")
        with self.assertRaises(fleet.RunError):
            fleet_graph.set_limits(state, budget=-3)
        fleet_graph.set_limits(state, budget=10)
        self.assertEqual(state["status"], "running")
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s1"])
        # A budget passed when the run starts replaces the fleet's.
        other = fleet.create("x", self.tmp.name, fleet_graph.get("auto"), budget=1.5)
        self.assertEqual(other["limits"]["budget"], 1.5)

    def test_the_step_limit(self):
        state = self.approved(plan_reply(3, "diamond"), repo=True, max_steps=4)
        # Two builders fit; the third would be the fifth node.
        self.assertEqual(fleet_graph.advance(state), ["builder.s1", "builder.s2"])
        self.assertEqual(state["reason"], "the run reached its limit of 4 steps")
        fleet_graph.set_limits(state, max_steps=20)
        self.assertEqual(fleet_graph.advance(state), ["builder.s3"])

    def test_extra_files_hold_the_slice_until_accepted(self):
        state = self.approved(plan_reply(files={"s1": ["src/", "part1.py"]}))
        fleet_graph.advance(state)
        self.answer(state, "builder.s1", example("builder"), changed=["part1.py", "src/a.py", "setup.cfg", "x.py"])
        self.assertEqual(state["nodes"]["builder.s1"]["scope"]["extra"], ["setup.cfg", "x.py"])
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual((state["status"], state["held_by"]), ("held", "builder.s1"))
        self.assertEqual(state["reason"], "builder.s1 changed files outside its slice: setup.cfg, x.py")
        for nid, reason, error in (("builder.s1", " ", "say why"), ("scout", "x", "no files outside")):
            with self.assertRaises(fleet.RunError) as caught:
                fleet_graph.accept_scope(state, nid, reason)
            self.assertIn(error, str(caught.exception))
        fleet_graph.accept_scope(state, "builder.s1", "the version bump belongs with it")
        self.assertEqual(state["status"], "running")
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s1"])
        self.assertIn("Files it changed outside the slice: setup.cfg, x.py (accepted by the person running this: "
                      "the version bump belongs with it)", fleet.brief(state, "reviewer.s1"))
        with self.assertRaises(fleet.RunError):
            fleet_graph.accept_scope(state, "builder.s1", "again")
        # A later build of the slice may touch what was accepted.
        self.answer(state, "reviewer.s1", example("reviewer"))
        fleet_graph.advance(state)
        self.answer(state, "builder.s1.2", example("builder"), changed=["part1.py", "x.py"])
        self.assertEqual(state["nodes"]["builder.s1.2"]["scope"]["extra"], [])

    def test_extra_files_sent_back(self):
        state = self.approved()
        fleet_graph.advance(state)
        self.answer(state, "builder.s1", example("builder"), changed=["part1.py", "x.py"])
        fleet_graph.advance(state)
        nid = fleet_graph.scope_send_back(state, "builder.s1")
        self.assertEqual((nid, state["status"]), ("builder.s1.2", "running"))
        self.assertIn("changed files outside it: x.py. Undo those changes", fleet.brief(state, nid))
        with self.assertRaises(fleet.RunError):
            fleet_graph.scope_send_back(state, "builder.s1")  # no longer the latest build


class DaemonTest(unittest.TestCase):
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
        self.told = []
        self.d.notifier = mock.Mock(tell=lambda *a: self.told.append(a))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def state(self):
        return fleet.load(self.run)

    def session_of(self, nid):
        return self.d.registry.sessions[self.state()["nodes"][nid]["session"]]

    def finish(self, nid, reply, work=None):
        session = self.session_of(nid)
        if work:
            work(session["cwd"])
        transcript = self.root / f"{session['id']}.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": block(reply)}]}}) + "\n")
        for status in ("working", "idle"):
            self.d.handle({"cmd": "update", "session_id": session["id"], "agent": "claude", "status": status,
                           "cwd": session["cwd"], "transcript_path": str(transcript)})

    def start(self, **extra):
        self.run = self.d.handle({"cmd": "fleet-start", "goal": "Make it better", "folder": str(self.repo),
                                  "fleet": "single-loop", **extra})["run"]["id"]
        self.finish("scout", example("scout"))
        self.finish("architect", plan_reply())
        self.d.handle({"cmd": "fleet-approve", "run": self.run})

    def test_extra_files_are_found_in_git_and_answered(self):
        self.start()
        self.finish("builder.s1", example("builder"), lambda cwd: (commit(cwd, "part1.py", "extra.py"),
                                                                    Path(cwd, "stray.txt").write_text("x\n")))
        state = self.state()
        self.assertEqual(state["reason"], "builder.s1 changed files outside its slice: extra.py, stray.txt")
        self.assertEqual(state["nodes"]["builder.s1"]["scope"]["changed"], ["extra.py", "part1.py", "stray.txt"])
        with mock.patch.object(self.d, "from_agent", lambda pid: "claude"):
            refused = self.d.handle_fleet("fleet-accept-scope", {"run": self.run, "node": "builder.s1",
                                                                  "reason": "x"}, peer=1)
        self.assertIn("inside an agent", refused["error"])
        response = self.d.handle({"cmd": "fleet-accept-scope", "run": self.run, "node": "builder.s1",
                                  "reason": "part of the fix"})
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.state()["nodes"]["reviewer.s1"]["status"], "running")
        # The stray file is left uncommitted: closing says so; the accepted files are fine.
        self.finish("reviewer.s1", PASS)
        failed = [w for ok, w in self.d.handle({"cmd": "fleet-close", "run": self.run, "check": True})["checks"]
                  if not ok]
        self.assertEqual(failed, ["the run's worktree has uncommitted changes"])

    def test_extra_files_sent_back_to_a_new_builder(self):
        self.start()
        self.finish("builder.s1", example("builder"), lambda cwd: commit(cwd, "part1.py", "extra.py"))
        response = self.d.handle({"cmd": "fleet-scope-back", "run": self.run, "node": "builder.s1"})
        self.assertTrue(response["ok"], response)
        self.assertIn("Undo those changes", self.session_of("builder.s1.2")["task"])

    def test_the_close_check_finds_files_outside_every_slice(self):
        self.start()
        self.finish("builder.s1", example("builder"), lambda cwd: commit(cwd, "part1.py"))
        self.finish("reviewer.s1", PASS)
        commit(self.state()["worktree"]["path"], "sneaked.py")  # after the builder's check
        failed = [w for ok, w in fleet_close.check(self.state()) if not ok]
        self.assertEqual(failed, ["files changed outside every slice: sneaked.py"])

    def test_costs_count_against_the_budget(self):
        with mock.patch.object(costs, "session_cost", lambda session: {"usd": 1.5, "real": True}):
            self.start(budget=2)
        state = self.state()
        self.assertEqual(state["nodes"]["scout"]["cost"], {"usd": 1.5, "real": True})
        self.assertEqual((state["status"], state["reason"]), ("held", "the run has spent $3.00, at its $2 budget"))
        self.assertEqual(state["nodes"]["architect"]["status"], "done")  # what was running finishes
        response = self.d.handle({"cmd": "fleet-limits", "run": self.run, "budget": 20})
        self.assertEqual(response["run"]["status"], "at-gate")  # released, and on to its plan gate

    def test_waiting_and_stalled_nodes(self):
        self.run = self.d.handle({"cmd": "fleet-start", "goal": "Make it better", "folder": str(self.repo)})["run"]["id"]
        session = self.session_of("scout")
        update = {"cmd": "update", "session_id": session["id"], "agent": "claude", "cwd": session["cwd"]}
        self.d.handle({**update, "status": "needs-input"})
        self.assertTrue(self.state()["nodes"]["scout"]["waiting"])
        self.d.handle({**update, "status": "working"})
        self.assertFalse(self.state()["nodes"]["scout"]["waiting"])
        quiet_since = self.d.registry.sessions[session["id"]]["updated"]
        self.d.fleet_watch(now=quiet_since + 19 * 60)
        self.assertFalse(self.state()["nodes"]["scout"].get("stalled"))
        self.d.fleet_watch(now=quiet_since + 21 * 60)
        self.assertEqual(self.state()["nodes"]["scout"]["stalled"], quiet_since)
        self.assertIn("scout looks stalled", self.told[-1][0])
        told = len(self.told)
        self.d.fleet_watch(now=quiet_since + 30 * 60)
        self.assertEqual(len(self.told), told)  # flagged once
        self.d.handle({**update, "status": "working"})  # a sign of life
        self.d.fleet_watch(now=self.d.registry.sessions[session["id"]]["updated"] + 60)
        self.assertIsNone(self.state()["nodes"]["scout"]["stalled"])
        self.assertIn("stalled", [e["event"] for e in fleet.read_activity(self.run)])


class TopTest(unittest.TestCase):
    def run_state(self, **changes):
        return {"id": "r1", "goal": "Make it better", "status": "held", "gate": None, "folder": "/w/app",
                "held_by": "builder.s1", "reason": "builder.s1 changed files outside its slice: x.py", "updated": 1,
                "limits": {"budget": 5, "max_steps": 30}, "shape": "single-loop",
                "nodes": {"builder.s1": {"id": "builder.s1", "role": "builder", "status": "done",
                                         "cost": {"usd": 1.25}, "scope": {"extra": ["x.py"], "accepted": None}}},
                **changes}

    def top_for(self, run):
        self.requests = []

        def request(payload, timeout=1.0):
            self.requests.append(payload)
            return {"ok": True, "run": run}
        t = top.Top(request=request, stop=lambda s: None, wait=lambda s: True, now=lambda: 10, agents=["claude"],
                    background=lambda work: work())
        t.apply({"ok": True, "sessions": [], "queue": {"tasks": []}, "away": None, "approvals": [], "fleets": [run]})
        t.switch("fleets")
        return t

    def test_answering_extra_files(self):
        t = self.top_for(self.run_state())
        self.assertIn(" a Accept… ", t.render(60, 20).text())
        t.key("a")
        for ch in "needed":
            t.key(ch)
        t.key("enter")
        self.assertEqual(self.requests[-1], {"cmd": "fleet-accept-scope", "run": "r1", "node": "builder.s1",
                                             "reason": "needed"})
        t.key("b")
        t.key("y")
        self.assertEqual(self.requests[-1], {"cmd": "fleet-scope-back", "run": "r1", "node": "builder.s1"})
        t.key("enter")
        self.assertIn("spent: $1.25 of $5 · 1 of 30 steps", t.render(60, 30).text())

    def test_raising_the_limits(self):
        t = self.top_for(self.run_state(held_by="limits", reason="the run has spent $6.00, at its $5 budget"))
        t.key("r")
        self.assertEqual(t.ask["value"], "5 30")
        t.ask["value"] = "12 40"
        t.key("enter")
        self.assertEqual(self.requests[-1], {"cmd": "fleet-limits", "run": "r1", "budget": 12.0, "max_steps": 40})
        t.key("r")
        t.ask["value"] = "lots"
        t.key("enter")
        self.assertIn("type the budget", t.render(60, 20).text())


if __name__ == "__main__":
    unittest.main()
