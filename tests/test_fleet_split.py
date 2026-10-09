"""Recursive slices: a builder splits its slice into smaller ones, which run
one level down before the slice is reviewed as a whole."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, fleet, fleet_graph, fleet_reply, fleet_report
from omaorchestra.app import present_fleet
from omaorchestra.registry import Registry


def block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def plan_reply(n=1, shape="single-loop", files=None):
    slices = [{"id": f"s{i}", "intent": f"Part {i}", "files": (files or {}).get(f"s{i}", [f"part{i}/"]),
               "done_when": "true -> passes", "risk": "low", "risk_why": "small"} for i in range(1, n + 1)]
    return example("architect", shape=shape, slices=slices, edges=[])


def split_reply(parent="part1", children=("a", "b"), edges=(), files=None):
    slices = [{"id": c, "intent": f"Piece {c}", "files": (files or {}).get(c, [f"{parent}/{c}.py"]),
               "done_when": f"test {c} -> passes", "risk": "low", "risk_why": "small"} for c in children]
    return {"status": "split", "changed": [],
            "split": {"rationale": "Two changes, checked apart.", "slices": slices,
                      "edges": [{"from": a, "to": b, "artifact": "x"} for a, b in edges]}}


REJECT = example("reviewer")
PASS = example("reviewer", verdict="PASS", findings=[])


class SplitReplyTest(unittest.TestCase):
    def test_a_split_is_a_valid_builder_reply(self):
        result = fleet_reply.parse("builder", block(split_reply(edges=[("a", "b")])))
        self.assertEqual((result["status"], result["done_when"]), ("split", None))
        self.assertEqual([s["id"] for s in result["split"]["slices"]], ["a", "b"])
        self.assertEqual(fleet_reply.parse("builder", block(fleet_reply.SPLIT_EXAMPLE))["status"], "split")

    def test_refusals(self):
        no_split = {"status": "split", "changed": []}
        one = split_reply(children=("a",))
        dup = split_reply(children=("a", "a"))
        loop = split_reply(edges=[("a", "b"), ("b", "a")])
        stray = split_reply(edges=[("a", "z")])
        outside = split_reply(files={"a": ["../x.py"]})
        no_command = example("builder")
        del no_command["done_when"]
        for reply, error in ((no_split, "split does not give the slices"), (one, "needs at least 2"),
                             (dup, "split: two slices share an id"), (loop, "split: the edges go round"),
                             (stray, "split: edge a -> z"), (outside, "must be a path inside"),
                             (no_command, "no done_when")):
            with self.subTest(error=error), self.assertRaises(fleet_reply.ReplyError) as caught:
                fleet_reply.parse("builder", block(reply))
            self.assertIn(error, str(caught.exception))

    def test_the_format_offers_a_split_only_when_allowed(self):
        self.assertNotIn("Or split the slice", fleet_reply.reply_format("builder"))
        self.assertIn("Or split the slice", fleet_reply.reply_format("builder", can_split=True))
        self.assertNotIn("Or split the slice", fleet_reply.reply_format("reviewer", can_split=True))

    def test_the_format_says_which_way_an_edge_goes_and_the_limits(self):
        # A live run's builder split with its edge backwards, and an architect
        # wrote a done_when over the limit twice.
        for text in (fleet_reply.reply_format("architect"), fleet_reply.reply_format("builder", can_split=True)):
            self.assertIn("`from` is the slice that produces it, `to` the one that uses it, so `from` is built "
                          "first", " ".join(text.split()))
            self.assertIn("1000 characters for `done_when`", text)


class SplitTemplateTest(unittest.TestCase):
    def test_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "fleets.toml"
            self.assertEqual((fleet_graph.get("auto", file)["max_depth"], fleet_graph.get("auto", file)["split_gate"]),
                             (1, False))
            file.write_text("[fleets.deep]\nmax_depth = 3\nsplit_gate = true\n")
            deep = fleet_graph.get("deep", file)
            self.assertEqual((deep["max_depth"], deep["split_gate"]), (3, True))
            for text, error in (("[fleets.x]\nmax_depth = 9\n", "max_depth must be"),
                                ("[fleets.x]\nsplit_gate = 1\n", "split_gate must be"),
                                ("[fleets.x]\npermission_mode = \"yes\"\n", "permission_mode must be")):
                file.write_text(text)
                with self.subTest(error=error), self.assertRaises(fleet_graph.FleetError) as caught:
                    fleet_graph.load(file)
                self.assertIn(error, str(caught.exception))


class SplitRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_of(self, plan=None, **template):
        state = fleet.create("Make it better", self.tmp.name, {**fleet_graph.get("auto"), "scout": False,
                                                                **template})
        state["repo"] = True
        self.assertEqual(fleet_graph.advance(state), ["architect"])
        self.answer(state, "architect", plan or plan_reply())
        fleet_graph.advance(state)
        fleet_graph.approve(state, "plan")
        return state

    def answer(self, state, nid, reply, changed=None):
        fleet.node_started(state, nid, f"S-{nid}", "claude")
        return fleet.record_reply(state, nid, block(reply), changed=changed)

    def step(self, state, nid, reply, then, changed=None):
        self.answer(state, nid, reply, changed)
        self.assertEqual(fleet_graph.advance(state), then)

    def test_a_split_runs_its_slices_then_reviews_the_whole(self):
        state = self.run_of(max_depth=2)
        self.assertEqual(fleet_graph.advance(state), ["builder.s1"])
        task = fleet.task_for(state, "builder.s1")
        self.assertIn("Or split the slice", task)
        self.assertLess(task.index("## Build it, or split it"), task.index("## Reply format"))  # up front
        self.step(state, "builder.s1", split_reply(edges=[("b", "a")], files={"b": ["part1/b.py"]}),
                  ["builder.s1-b"])  # b first: a uses what it makes
        self.assertEqual((fleet.depth(state, "s1-b"), fleet.root_slice(state, "s1-b")), (1, "s1"))
        self.assertNotIn("Or split the slice", fleet.task_for(state, "builder.s1-b"))  # depth 2 is the limit
        brief = fleet.brief(state, "builder.s1-b")
        self.assertNotIn("## Build it, or split it", brief)
        self.assertIn("Slice s1-b: Piece b", brief)
        self.assertIn("split slice s1 (Part 1)", brief)
        self.assertIn("- s1-a: Piece a", brief)
        self.step(state, "builder.s1-b", example("builder", changed=["part1/b.py"]), ["reviewer.s1-b"])
        self.step(state, "reviewer.s1-b", REJECT, ["builder.s1-b.2"])  # a smaller slice is sent back on its own
        self.step(state, "builder.s1-b.2", example("builder"), ["reviewer.s1-b.2"])
        self.step(state, "reviewer.s1-b.2", PASS, ["builder.s1-a"])
        self.step(state, "builder.s1-a", example("builder", changed=["part1/a.py"]), ["reviewer.s1-a"])
        self.step(state, "reviewer.s1-a", PASS, ["reviewer.s1"])  # all passed: the whole slice
        review = fleet.brief(state, "reviewer.s1")
        self.assertIn("It split the slice into 2 smaller slices", review)
        self.assertIn("- s1-b: Piece b; latest review: PASS; changed part1/b.py", review)
        self.assertIn("Review slice s1 as a whole", review)
        self.assertEqual([(r["slice"], r["depth"], r["build"]) for r in fleet.board(state)],
                         [("s1", 0, "split"), ("s1-a", 1, "done"), ("s1-b", 1, "done")])
        self.step(state, "reviewer.s1", PASS, [])
        self.assertEqual(state["status"], "done")
        report = fleet_report.report(state, fleet.read_activity(state["id"]))
        self.assertEqual([(r["slice"], r["depth"], r["builds"]) for r in report["slices"]],
                         [("s1", 0, 1), ("s1-a", 1, 1), ("s1-b", 1, 2)])
        split = next(e for e in fleet.read_activity(state["id"]) if e["event"] == "split")
        self.assertEqual((split["node"], split["slices"]), ("builder.s1", ["s1-a", "s1-b"]))

    def test_the_whole_rejected_goes_back_to_a_builder_that_may_split_again(self):
        state = self.run_of(max_depth=2)
        fleet_graph.advance(state)
        self.step(state, "builder.s1", split_reply(), ["builder.s1-a"])
        self.step(state, "builder.s1-a", example("builder"), ["reviewer.s1-a"])
        self.step(state, "reviewer.s1-a", PASS, ["builder.s1-b"])
        self.step(state, "builder.s1-b", example("builder"), ["reviewer.s1-b"])
        self.step(state, "reviewer.s1-b", PASS, ["reviewer.s1"])
        self.step(state, "reviewer.s1", REJECT, ["builder.s1.2"])
        self.step(state, "builder.s1.2", split_reply(), ["builder.s1-2a"])  # new ids: the old nodes are not reused
        self.assertEqual(fleet.slice_of(state, "s1-a")["parent"], "s1")  # the first split's slices are still found

    def test_a_held_smaller_slice_holds_the_run_by_its_own_id(self):
        state = self.run_of(max_depth=2, tries=1)
        fleet_graph.advance(state)
        self.step(state, "builder.s1", split_reply(), ["builder.s1-a"])
        self.step(state, "builder.s1-a", example("builder"), ["reviewer.s1-a"])
        self.step(state, "reviewer.s1-a", REJECT, [])
        self.assertEqual((state["status"], state["held_by"]), ("held", "s1-a"))
        self.assertEqual(fleet_graph.retry(state, "try harder"), "builder.s1-a.2")

    def test_a_split_that_breaks_the_rules_holds_as_a_bad_reply(self):
        for template, reply, changed, error in (
                ({}, split_reply(), [], "fleet allows no splits"),
                ({"max_depth": 2}, split_reply(files={"a": ["other/a.py"]}), [], "reaches outside slice s1's files"),
                ({"max_depth": 2}, split_reply(), ["part1/a.py"], "a split must change nothing")):
            with self.subTest(error=error):
                state = self.run_of(**template)
                fleet_graph.advance(state)
                n = self.answer(state, "builder.s1", reply, changed)
                self.assertEqual((n["status"], n["error_kind"], state["status"]), ("held", "reply", "held"))
                self.assertIn(error, state["reason"])

    def test_a_retry_after_a_refused_reply_is_told_why(self):
        state = self.run_of()
        fleet_graph.advance(state)
        self.answer(state, "builder.s1", split_reply())
        self.assertEqual(fleet_graph.retry(state, "build it"), "builder.s1.2")
        self.assertEqual(state["nodes"]["builder.s1.2"]["feedback"], [
            "omaorchestra refused the last builder's reply: slice s1 cannot be split: this run's fleet allows no "
            "splits. Give a reply that avoids this.", "build it"])
        self.assertIn("omaorchestra refused the last builder's reply", fleet.task_for(state, "builder.s1.2"))

    def test_deeper_splits_stop_at_the_depth(self):
        state = self.run_of(max_depth=3)
        fleet_graph.advance(state)
        self.step(state, "builder.s1", split_reply(files={"a": ["part1/a/"], "b": ["part1/b/"]}), ["builder.s1-a"])
        self.assertIn("Or split the slice", fleet.task_for(state, "builder.s1-a"))
        self.step(state, "builder.s1-a", split_reply("part1/a", ("x", "y")), ["builder.s1-a-a"])
        self.assertEqual(fleet.depth(state, "s1-a-a"), 2)
        n = self.answer(state, "builder.s1-a-a", split_reply("part1/a/x"))
        self.assertIn("splits 2 levels deep", n["error"])

    def test_the_step_limit_stops_a_split(self):
        # architect, builder.s1, builder.s1-a: the third step; reviewer.s1-a would be a fourth.
        state = self.run_of(max_depth=2, max_steps=3)
        fleet_graph.advance(state)
        self.step(state, "builder.s1", split_reply(), ["builder.s1-a"])
        self.step(state, "builder.s1-a", example("builder"), [])
        self.assertEqual((state["status"], state["held_by"]), ("held", "limits"))
        self.assertIn("limit of 3 steps", state["reason"])
        fleet_graph.set_limits(state, max_steps=10)
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s1-a"])  # raised: the split goes on

    def test_the_split_gate(self):
        state = self.run_of(max_depth=2, split_gate=True)
        fleet_graph.advance(state)
        self.step(state, "builder.s1", split_reply(), [])
        self.assertEqual((state["status"], state["gate"], state["split_node"]), ("at-gate", "split", "builder.s1"))
        card = present_fleet.cards(state)[0]
        self.assertEqual((card["kind"], card["node"], [c["id"] for c in card["slices"]]),
                         ("split", "builder.s1", ["s1-a", "s1-b"]))
        fleet_graph.approve(state, "split", note="fine")
        self.assertIn("split:builder.s1", state["approved"])
        self.assertEqual(fleet_graph.advance(state), ["builder.s1-a"])

    def test_the_architect_is_told_when_builders_may_split(self):
        for depth, told in ((1, False), (2, True)):
            state = fleet.create("Make it better", self.tmp.name, {**fleet_graph.get("auto"), "scout": False,
                                                                    "max_depth": depth})
            fleet_graph.advance(state)
            self.assertEqual("## Builders may split slices" in fleet.brief(state, "architect"), told)

    def test_old_runs_without_a_depth_advance_as_before(self):
        state = self.run_of()
        del state["template"]["max_depth"]
        self.assertEqual(fleet_graph.advance(state), ["builder.s1"])
        self.assertFalse(fleet.can_split(state, "s1"))


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


class DaemonSplitTest(unittest.TestCase):
    """A split in a diamond, driven by the daemon: the smaller slices work in
    the worktree of the slice they came from, and git checks the split."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("x = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")
        (self.root / "c.toml").write_text("")
        (self.root / "fleets.toml").write_text('[fleets.deep]\nshape = "diamond"\nscout = false\nmax_depth = 2\n')
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml"),
                                                "OMAORCHESTRA_WORKTREES": str(self.root / "wt"),
                                                "OMAORCHESTRA_CLAUDE": "true"})
        self.env.start()
        self.d = daemon.Daemon(Registry(self.root / "state" / "sessions.json"), is_alive=lambda p, s: True)
        self.d.spawn = lambda cmd, **kw: None
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None
        self.d.settings["tasks"]["max_parallel"] = 5
        self.d.user_path = os.environ["PATH"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def finish(self, run_id, nid, reply):
        session_id = fleet.load(run_id)["nodes"][nid]["session"]
        self.assertTrue(session_id, f"{nid} has not started")
        transcript = self.root / f"{session_id}.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": block(reply)}]}}) + "\n")
        session = self.d.registry.sessions[session_id]
        for status in ("working", "idle"):
            self.d.handle({"cmd": "update", "session_id": session_id, "agent": "claude", "status": status,
                           "cwd": session["cwd"], "transcript_path": str(transcript)})
        return session

    def test_split_slices_work_in_their_slices_worktree(self):
        response = self.d.handle({"cmd": "fleet-start", "goal": "Make it better", "folder": str(self.repo),
                                  "fleet": "deep"})
        self.assertTrue(response["ok"], response)
        run_id = response["run"]["id"]
        self.finish(run_id, "architect", plan_reply(3, shape="diamond"))
        self.d.handle({"cmd": "fleet-approve", "run": run_id})
        s1 = fleet.load(run_id)["slice_worktrees"]["s1"]
        # A split that leaves a stray file is refused, until the file is gone.
        (Path(s1["workdir"]) / "part1").mkdir()
        (Path(s1["workdir"]) / "part1" / "stray.py").write_text("")
        self.finish(run_id, "builder.s1", split_reply())
        state = fleet.load(run_id)
        self.assertEqual(state["status"], "held")
        self.assertIn("a split must change nothing", state["reason"])
        (Path(s1["workdir"]) / "part1" / "stray.py").unlink()
        self.finish(run_id, "builder.s1", split_reply())
        state = fleet.load(run_id)
        self.assertEqual(state["status"], "running")
        child = self.d.registry.sessions[state["nodes"]["builder.s1-a"]["session"]]
        self.assertEqual(child["cwd"], s1["workdir"])
        # Its brief names the worktree, not the run's folder (a live run's
        # builder first wrote in the folder).
        self.assertIn(f"You work in {s1['workdir']}, a git worktree of it on branch {s1['branch']}: make every "
                      f"change and run every command there, never in {self.repo}.", " ".join(child["task"].split()))
        architect = self.d.registry.sessions[state["nodes"]["architect"]["session"]]
        self.assertIn(f"You are the architect in a fleet run of omaorchestra, in {self.repo}.", architect["task"])
        self.assertNotIn("s1-a", state["slice_worktrees"])


    def test_agents_run_in_auto_mode_unless_their_role_says(self):
        # Live runs stopped for a yes at each command the agent ran.
        spawned = []
        self.d.spawn = lambda cmd, **kw: spawned.append((" ".join(map(str, cmd)), kw["env"]))
        roles_dir = self.root / "roles"
        roles_dir.mkdir()
        (roles_dir / "careful.md").write_text("---\nname: careful\npermissionMode: acceptEdits\n---\n\nPlan it.\n")
        for agent in ("opencode", "codex"):
            (roles_dir / f"{agent}-planner.md").write_text(f"---\nname: {agent}-planner\nagent: {agent}\n"
                                                           "permissionMode: acceptEdits\n---\n\nPlan it.\n"
                                                           if agent == "codex" else
                                                           f"---\nname: {agent}-planner\nagent: {agent}\n---\n\n"
                                                           "Plan it.\n")
        (self.root / "fleets.toml").write_text(
            '[fleets.deep]\nscout = false\n[fleets.manual]\nscout = false\npermission_mode = "manual"\n'
            '[fleets.careful]\nscout = false\n[fleets.careful.roles]\narchitect = "careful"\n'
            '[fleets.open]\nscout = false\n[fleets.open.roles]\narchitect = "opencode-planner"\n'
            '[fleets.openmanual]\nscout = false\npermission_mode = "manual"\n'
            '[fleets.openmanual.roles]\narchitect = "opencode-planner"\n'
            '[fleets.codex]\nscout = false\n[fleets.codex.roles]\narchitect = "codex-planner"\n')
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_OPENCODE": "true", "OMAORCHESTRA_CODEX": "true"}):
            for name, mode in (("deep", "auto"), ("manual", "manual"), ("careful", "acceptEdits"), ("open", None),
                               ("openmanual", None), ("codex", None)):
                with self.subTest(fleet=name):
                    response = self.d.handle({"cmd": "fleet-start", "goal": f"Make it {name}",
                                              "folder": str(self.repo), "fleet": name})
                    self.assertTrue(response["ok"], response)
                    command, env = spawned[-1]
                    if mode:
                        self.assertIn(f"--permission-mode {mode} ", command)
                    else:  # opencode's mode is in its agent's permissions; Codex keeps its own
                        self.assertNotIn("--permission-mode", command)
                        self.assertNotIn(" -a ", command)
                    if name.startswith("open"):
                        agent = json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"]["omaorchestra-opencode-planner"]
                        self.assertEqual(agent.get("permission", {}).get("external_directory"),
                                         "allow" if name == "open" else None)


if __name__ == "__main__":
    unittest.main()
