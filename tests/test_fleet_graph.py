import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, fleet, fleet_graph, fleet_reply, worktrees
from omaorchestra.registry import Registry


def block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def plan_reply(n=2, shape="single-loop", edges=(), files=None):
    slices = [{"id": f"s{i}", "intent": f"Part {i}", "files": (files or {}).get(f"s{i}", [f"part{i}.py"]),
               "done_when": "true -> passes", "risk": "low", "risk_why": "small"} for i in range(1, n + 1)]
    return example("architect", shape=shape, slices=slices,
                   edges=[{"from": a, "to": b, "artifact": "x"} for a, b in edges])


REJECT = example("reviewer")
PASS = example("reviewer", verdict="PASS", findings=[])


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "fleets.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_built_in_and_yours(self):
        self.assertEqual(set(fleet_graph.load(self.file)), {"auto", "single-loop", "diamond"})
        self.file.write_text('[fleets.careful]\nshape = "single-loop"\nscout = false\ntries = 3\n'
                             '[fleets.careful.roles]\nreviewer = "security-reviewer"\n'
                             '[fleets.auto]\ndescription = "mine"\n')
        fleets = fleet_graph.load(self.file)
        careful = fleets["careful"]
        self.assertEqual((careful["shape"], careful["scout"], careful["tries"], careful["builtin"]),
                         ("single-loop", False, 3, False))
        self.assertEqual(careful["roles"], {"scout": "scout", "architect": "architect", "builder": "builder",
                                            "reviewer": "security-reviewer", "integrator": "integrator"})
        self.assertEqual(fleets["auto"]["description"], "mine")
        with self.assertRaises(fleet_graph.FleetError):
            fleet_graph.get("nope", self.file)

    def test_refusals(self):
        for text, error in (('[fleets.x]\nshape = "star"\n', "shape must be"),
                            ('[fleets.x]\ntries = 9\n', "tries must be"),
                            ('[fleets.x]\nscout = "yes"\n', "scout must be"),
                            ('[fleets.x]\ncolour = "red"\n', "unknown colour"),
                            ('[fleets.x.roles]\ntester = "t"\n', "no stage tester"),
                            ('[fleets.x.roles]\nbuilder = "a b"\n', "role's name"),
                            ('[other]\n', "only [fleets"), ("not toml [", "fleets.toml")):
            self.file.write_text(text)
            with self.subTest(error=error), self.assertRaises(fleet_graph.FleetError) as caught:
                fleet_graph.load(self.file)
            self.assertIn(error, str(caught.exception))


class ShapeTest(unittest.TestCase):
    def test_order_follows_edges(self):
        plan = plan_reply(3, edges=[("s3", "s1")])
        self.assertEqual(fleet_graph.order(plan), ["s2", "s3", "s1"])
        with self.assertRaises(fleet.RunError):
            fleet_graph.order(plan_reply(2, edges=[("s1", "s2"), ("s2", "s1")]))

    def test_diamond_problems(self):
        self.assertIsNone(fleet_graph.diamond_problem(plan_reply(3), True))
        self.assertIn("not in a git repository", fleet_graph.diamond_problem(plan_reply(3), False))
        self.assertIn("2 slices", fleet_graph.diamond_problem(plan_reply(2), True))
        self.assertEqual(fleet_graph.diamond_problem(plan_reply(3, files={"s3": ["./part1.py"]}), True),
                         "s1 and s3 both touch ./part1.py")
        self.assertEqual(fleet_graph.diamond_problem(plan_reply(3, files={"s1": ["src/"], "s2": ["src/a.py"]}), True),
                         "s1 and s2 both touch src/a.py")
        self.assertIn("s3 depends on more than one",
                      fleet_graph.diamond_problem(plan_reply(3, edges=[("s1", "s3"), ("s2", "s3")]), True))
        self.assertIsNone(fleet_graph.diamond_problem(plan_reply(3, edges=[("s1", "s3")]), True))


class AdvanceTest(unittest.TestCase):
    """The engine on its own: replies are recorded straight into the state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_of(self, fleet_name="auto", repo=True, **template):
        state = fleet.create("Make it better", self.tmp.name, {**fleet_graph.get(fleet_name), **template})
        state["repo"] = repo
        return state

    def answer(self, state, nid, reply):
        fleet.node_started(state, nid, f"S-{nid}", "claude")
        fleet.record_reply(state, nid, block(reply))

    def to_the_gate(self, state, plan):
        self.assertEqual(fleet_graph.advance(state), ["scout"])
        self.assertEqual(fleet_graph.advance(state), [])  # the scout is still working
        self.answer(state, "scout", example("scout"))
        self.assertEqual(fleet_graph.advance(state), ["architect"])
        self.answer(state, "architect", plan)
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual((state["status"], state["gate"]), ("at-gate", "plan"))

    def test_single_loop_one_slice_at_a_time(self):
        state = self.run_of()
        self.to_the_gate(state, plan_reply(2, edges=[("s2", "s1")]))
        self.assertEqual(state["shape"], "single-loop")
        with self.assertRaises(fleet.RunError):
            fleet_graph.approve(state, "merge")
        fleet_graph.approve(state, "plan", note="go")
        self.assertEqual(state["approved"]["plan"]["note"], "go")
        self.assertEqual(fleet_graph.advance(state), ["builder.s2"])  # s1 waits for s2
        self.answer(state, "builder.s2", example("builder"))
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s2"])
        self.answer(state, "reviewer.s2", REJECT)
        self.assertEqual(fleet_graph.advance(state), ["builder.s2.2"])  # sent back
        self.answer(state, "builder.s2.2", example("builder"))
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s2.2"])
        self.answer(state, "reviewer.s2.2", PASS)
        self.assertEqual(fleet_graph.advance(state), ["builder.s1"])
        self.answer(state, "builder.s1", example("builder"))
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s1"])
        self.answer(state, "reviewer.s1", PASS)
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual(state["status"], "done")
        self.assertNotIn("integrator", state["nodes"])  # a single loop has nothing to merge
        events = [e["event"] for e in fleet.read_activity(state["id"])]
        self.assertEqual((events.count("gate"), events.count("approved"), events[-1]), (1, 1, "finished"))

    def test_rejected_too_often_holds(self):
        state = self.run_of()
        self.to_the_gate(state, plan_reply(1))
        fleet_graph.approve(state, "plan")
        for attempt in ("builder.s1", "builder.s1.2"):
            self.assertEqual(fleet_graph.advance(state), [attempt])
            self.answer(state, attempt, example("builder"))
            fleet_graph.advance(state)
            self.answer(state, attempt.replace("builder", "reviewer"), REJECT)
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual((state["status"], state["held_by"]), ("held", "s1"))
        self.assertIn("rejected s1 2 times: src/auth/session.py:48", state["reason"])

    def test_one_try_only(self):
        state = self.run_of(tries=1)
        self.to_the_gate(state, plan_reply(1))
        fleet_graph.approve(state, "plan")
        fleet_graph.advance(state)
        self.answer(state, "builder.s1", example("builder"))
        fleet_graph.advance(state)
        self.answer(state, "reviewer.s1", REJECT)
        fleet_graph.advance(state)
        self.assertIn("rejected s1 1 time:", state["reason"])

    def test_a_blocked_builder_holds(self):
        state = self.run_of()
        self.to_the_gate(state, plan_reply(1))
        fleet_graph.approve(state, "plan")
        fleet_graph.advance(state)
        self.answer(state, "builder.s1", example("builder", status="blocked", blocked="needs db/schema.sql",
                                                 done_when={"command": "t", "output": "", "passed": False}))
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual(state["reason"], "builder.s1 is blocked: needs db/schema.sql")

    def test_diamond(self):
        state = self.run_of()
        self.to_the_gate(state, plan_reply(3, shape="diamond", edges=[("s1", "s3")]))
        self.assertEqual((state["shape"], state["shape_note"]), ("diamond", None))
        fleet_graph.approve(state, "plan")
        self.assertEqual(fleet_graph.advance(state), ["builder.s1", "builder.s2"])  # s3 waits for s1
        self.answer(state, "builder.s2", example("builder"))
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s2"])
        self.answer(state, "reviewer.s2", PASS)
        self.answer(state, "builder.s1", example("builder"))
        self.assertEqual(fleet_graph.advance(state), ["reviewer.s1"])
        self.answer(state, "reviewer.s1", PASS)
        self.assertEqual(fleet_graph.advance(state), ["builder.s3"])
        self.answer(state, "builder.s3", example("builder"))
        fleet_graph.advance(state)
        self.answer(state, "reviewer.s3", PASS)
        self.assertEqual(fleet_graph.advance(state), [])
        self.assertEqual((state["status"], state["gate"]), ("at-gate", "merge"))
        fleet_graph.approve(state, "merge")
        self.assertEqual(fleet_graph.advance(state), ["integrator"])
        self.answer(state, "integrator", example("integrator"))
        fleet_graph.advance(state)
        self.assertEqual(state["status"], "done")

    def test_integrator_holds_on_a_red_suite_or_an_escalation(self):
        for reply, reason in ((example("integrator", suite={"command": "t", "output": "1 failed", "passed": False},
                                       blocked="merging s2 broke login"), "integrator: merging s2 broke login"),
                              (example("integrator", escalate="s1 and s2 disagree on the schema"),
                               "integrator: s1 and s2 disagree on the schema")):
            state = self.run_of()
            self.to_the_gate(state, plan_reply(3, shape="diamond"))
            fleet_graph.approve(state, "plan")
            for sid in ("s1", "s2", "s3"):
                fleet_graph.advance(state)
                self.answer(state, f"builder.{sid}", example("builder"))
                fleet_graph.advance(state)
                self.answer(state, f"reviewer.{sid}", PASS)
            fleet_graph.advance(state)
            fleet_graph.approve(state, "merge")
            fleet_graph.advance(state)
            self.answer(state, "integrator", reply)
            fleet_graph.advance(state)
            self.assertEqual((state["status"], state["reason"]), ("held", reason))

    def test_a_diamond_it_cannot_run_becomes_a_single_loop(self):
        state = self.run_of(repo=False)
        self.to_the_gate(state, plan_reply(3, shape="diamond"))
        self.assertEqual(state["shape"], "single-loop")
        self.assertIn("not in a git repository", state["shape_note"])

    def test_the_fleet_can_force_the_shape_and_skip_the_scout(self):
        state = self.run_of("diamond", scout=False)
        self.assertEqual(fleet_graph.advance(state), ["architect"])
        self.assertIn("uses the diamond shape", fleet.brief(state, "architect"))
        self.answer(state, "architect", plan_reply(3, shape="single-loop"))
        fleet_graph.advance(state)
        self.assertEqual(state["shape"], "diamond")
        forced = self.run_of("single-loop")
        self.to_the_gate(forced, plan_reply(3, shape="diamond"))
        self.assertEqual((forced["shape"], forced["shape_note"]), ("single-loop", None))

    def test_nothing_moves_while_held_or_after_cancel(self):
        state = self.run_of()
        fleet_graph.advance(state)
        fleet.node_started(state, "scout", "S", "claude")
        fleet.record_reply(state, "scout", "no block")
        self.assertEqual(fleet_graph.advance(state), [])
        fleet.record_reply(state, "scout", block(example("scout")))
        self.assertEqual(fleet_graph.advance(state), ["architect"])
        self.assertEqual([n["id"] for n in fleet_graph.cancel(state)], ["architect"])
        self.assertEqual((state["status"], state["nodes"]["architect"]["status"]), ("cancelled", "cancelled"))
        self.assertEqual(fleet_graph.advance(state), [])
        with self.assertRaises(fleet.RunError):
            fleet_graph.cancel(state)


class DaemonRunTest(unittest.TestCase):
    """The daemon drives a run: nodes go through the queue, sessions report,
    replies come from transcripts, worktrees are made."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.py").write_text("x = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml"),
                                                "OMAORCHESTRA_WORKTREES": str(self.root / "wt"),
                                                "OMAORCHESTRA_CLAUDE": "true"})
        self.env.start()
        self.d = daemon.Daemon(Registry(self.root / "state" / "sessions.json"), is_alive=lambda p, s: True)
        self.spawned = []
        self.d.spawn = lambda cmd, **kw: self.spawned.append(cmd)
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None
        self.d.settings["tasks"]["max_parallel"] = 5
        self.d.user_path = os.environ["PATH"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def start(self, **extra):
        response = self.d.handle({"cmd": "fleet-start", "goal": "Make it better", "folder": str(self.repo), **extra})
        self.assertTrue(response["ok"], response)
        return response["run"]["id"]

    def state(self, run_id):
        return fleet.load(run_id)

    def finish(self, run_id, nid, reply):
        """The node's session works, then goes idle with `reply` as its last message."""
        session_id = self.state(run_id)["nodes"][nid]["session"]
        self.assertTrue(session_id, f"{nid} has not started")
        transcript = self.root / f"{session_id}.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": block(reply)}]}}) + "\n")
        session = self.d.registry.sessions[session_id]
        for status in ("working", "idle"):
            self.d.handle({"cmd": "update", "session_id": session_id, "agent": "claude", "status": status,
                           "cwd": session["cwd"], "transcript_path": str(transcript)})

    def command_of(self, run_id, nid):
        session = self.d.registry.sessions[self.state(run_id)["nodes"][nid]["session"]]
        return session

    def test_a_single_loop_run(self):
        run_id = self.start()
        state = self.state(run_id)
        scout = state["nodes"]["scout"]
        self.assertEqual((scout["status"], scout["runs_as"], scout["agent"]), ("running", "scout", "claude"))
        command = self.spawned[0]
        self.assertEqual(command[command.index("--agent") + 1], "scout")
        self.assertIn("## Reply format", command[-1])
        session = self.command_of(run_id, "scout")
        self.assertEqual((session["fleet"], session["node"], session["role"]), (run_id, "scout", "scout"))
        self.assertEqual(session["cwd"], str(self.repo.resolve()))

        self.finish(run_id, "scout", example("scout"))
        self.finish(run_id, "architect", plan_reply(2))
        state = self.state(run_id)
        self.assertEqual((state["status"], state["gate"]), ("at-gate", "plan"))
        self.assertEqual(len(self.spawned), 2)  # nothing past the gate

        response = self.d.handle({"cmd": "fleet-approve", "run": run_id[:15]})
        self.assertTrue(response["ok"], response)
        state = self.state(run_id)
        self.assertEqual(state["branch"], f"omaorchestra/fleet-{run_id}")
        builder = self.command_of(run_id, "builder.s1")
        self.assertEqual(builder["cwd"], state["worktree"]["workdir"])
        self.assertEqual(builder["worktree"], state["worktree"]["path"])
        self.assertEqual(worktrees.find(state["branch"])["fleet"], run_id)

        self.finish(run_id, "builder.s1", example("builder"))
        self.assertEqual(self.state(run_id)["nodes"]["builder.s1"]["git"]["branch"], state["branch"])
        self.finish(run_id, "reviewer.s1", PASS)
        self.finish(run_id, "builder.s2", example("builder"))
        self.finish(run_id, "reviewer.s2", PASS)
        self.assertEqual(self.state(run_id)["status"], "done")
        self.assertEqual(self.d.handle({"cmd": "fleet-list"})["runs"][0]["id"], run_id)
        shown = self.d.handle({"cmd": "fleet-show", "run": run_id})
        self.assertEqual(shown["activity"][-1]["event"], "finished")

    def test_a_diamond_gets_a_worktree_per_slice(self):
        run_id = self.start(fleet="diamond")
        self.finish(run_id, "scout", example("scout"))
        self.finish(run_id, "architect", plan_reply(3, shape="diamond", edges=[("s1", "s3")]))
        self.d.handle({"cmd": "fleet-approve", "run": run_id})
        state = self.state(run_id)
        self.assertEqual(set(state["slice_worktrees"]), {"s1", "s2"})
        s1 = state["slice_worktrees"]["s1"]
        self.assertEqual(s1["branch"], f"omaorchestra/fleet-{run_id}-s1")
        self.assertEqual(worktrees.find(s1["branch"])["base_branch"], state["branch"])
        self.assertEqual(self.command_of(run_id, "builder.s1")["cwd"], s1["workdir"])
        # s1's builder commits; s3, which depends on it, starts from its branch.
        (Path(s1["workdir"]) / "part1.py").write_text("done = True\n")
        git(s1["workdir"], "add", ".")
        git(s1["workdir"], "commit", "-qm", "s1")
        self.finish(run_id, "builder.s1", example("builder"))
        self.assertEqual(self.command_of(run_id, "reviewer.s1")["cwd"], s1["workdir"])
        self.finish(run_id, "reviewer.s1", PASS)
        state = self.state(run_id)
        self.assertTrue((Path(state["slice_worktrees"]["s3"]["workdir"]) / "part1.py").exists())
        for sid in ("s2", "s3"):
            self.finish(run_id, f"builder.{sid}", example("builder"))
            self.finish(run_id, f"reviewer.{sid}", PASS)
        self.assertEqual(self.state(run_id)["gate"], "merge")
        self.d.handle({"cmd": "fleet-approve", "run": run_id, "gate": "merge"})
        self.assertEqual(self.command_of(run_id, "integrator")["cwd"], self.state(run_id)["worktree"]["workdir"])
        self.finish(run_id, "integrator", example("integrator"))
        self.assertEqual(self.state(run_id)["status"], "done")

    def test_a_node_that_cannot_start_holds_until_it_does(self):
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_CLAUDE": "no-such-agent"}):
            run_id = self.start()
        state = self.state(run_id)
        self.assertEqual((state["status"], state["held_by"]), ("held", "scout"))
        self.assertIn("is not installed", state["reason"])
        item = next(t for t in self.d.queue.tasks if t.get("node") == "scout")
        self.d.handle({"cmd": "queue-resume", "id": item["id"]})  # retried, and it starts now
        state = self.state(run_id)
        self.assertEqual((state["status"], state["nodes"]["scout"]["status"]), ("running", "running"))

    def test_cancelling(self):
        self.d.settings["tasks"]["max_parallel"] = 0  # nothing starts
        run_id = self.start()
        self.assertEqual([t["node"] for t in self.d.queue.tasks], ["scout"])
        self.assertTrue(self.d.handle({"cmd": "fleet-cancel", "run": run_id})["ok"])
        self.assertEqual(self.d.queue.tasks, [])
        self.assertEqual(self.state(run_id)["status"], "cancelled")
        self.assertFalse(self.d.handle({"cmd": "fleet-cancel", "run": run_id})["ok"])
        # Cancelling a node's queued task holds its run.
        other = self.start()
        item = self.d.queue.tasks[0]
        self.d.handle({"cmd": "queue-cancel", "id": item["id"]})
        state = self.state(other)
        self.assertEqual((state["status"], state["nodes"]["scout"]["status"]), ("held", "cancelled"))
        self.assertIn("cancelled", state["reason"])

    def test_refusals(self):
        for request, error in (({"cmd": "fleet-start", "goal": "x", "folder": str(self.repo), "fleet": "nope"},
                                "no fleet nope"),
                               ({"cmd": "fleet-start", "goal": " ", "folder": str(self.repo)}, "goal is empty"),
                               ({"cmd": "fleet-approve", "run": "nothing"}, "no run matching"),
                               ({"cmd": "fleet-nonsense", "run": "x"}, "no run matching")):
            response = self.d.handle(request)
            self.assertFalse(response["ok"])
            self.assertIn(error, response["error"])
        run_id = self.start()
        self.assertIn("not waiting at the plan gate",
                      self.d.handle({"cmd": "fleet-approve", "run": run_id, "gate": "plan"})["error"])


if __name__ == "__main__":
    unittest.main()
