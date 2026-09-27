import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import config, daemon, fleet, fleet_graph, fleet_reply, remote, top
from omaorchestra.registry import Registry


def block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def plan_reply(n=3, shape="diamond", edges=()):
    slices = [{"id": f"s{i}", "intent": f"Part {i}", "files": [f"part{i}.py"], "done_when": "true -> passes",
               "risk": "high" if i == 1 else "low", "risk_why": "small"} for i in range(1, n + 1)]
    return example("architect", shape=shape, slices=slices, not_doing=["Rewriting the parser"],
                   approve="Three parts, in parallel", edges=[{"from": a, "to": b, "artifact": "x"} for a, b in edges])


class StateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml"),
                                                "OMAORCHESTRA_WORKTREES": str(self.root / "wt"),
                                                "OMAORCHESTRA_CLAUDE": "true"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def at_gate(self, plan=None, repo=True):
        """A run waiting at its plan gate."""
        state = fleet.create("Make search faster", self.tmp.name, fleet_graph.get("auto"))
        state["repo"] = repo
        for nid, reply in (("scout", example("scout")), ("architect", plan or plan_reply())):
            fleet_graph.advance(state)
            fleet.node_started(state, nid, f"S-{nid}", "claude")
            fleet.record_reply(state, nid, block(reply))
        fleet_graph.advance(state)
        self.assertEqual(state["gate"], "plan")
        return state


class GateTest(StateDir):
    def test_send_back_runs_a_new_architect_with_the_note(self):
        state = self.at_gate()
        fleet_graph.drop(state, "s3")
        with self.assertRaises(fleet.RunError):
            fleet_graph.send_back(state, "   ")
        nid = fleet_graph.send_back(state, "Keep   the old index")
        self.assertEqual((nid, state["status"], state["gate"], state["dropped"]), ("architect.2", "running", None, []))
        brief = fleet.brief(state, nid)
        self.assertIn("Keep the old index", brief)
        self.assertIn("## Your plan before", brief)
        with self.assertRaises(fleet.RunError):
            fleet_graph.send_back(state, "again")  # not at the gate any more
        fleet.node_started(state, nid, "S-a2", "claude")
        fleet.record_reply(state, nid, block(plan_reply(2, shape="single-loop")))
        fleet_graph.advance(state)
        self.assertEqual((state["gate"], [s["id"] for s in fleet.live_plan(state)["slices"]]), ("plan", ["s1", "s2"]))
        self.assertIn("sent-back", [e["event"] for e in fleet.read_activity(state["id"])])

    def test_drop_a_slice(self):
        state = self.at_gate(plan_reply(4, edges=[("s1", "s4")]))
        self.assertEqual(state["shape"], "diamond")
        for slice_id, error in (("s9", "no slice s9"), ("s1", "s4 depends on s1")):
            with self.assertRaises(fleet.RunError) as caught:
                fleet_graph.drop(state, slice_id)
            self.assertIn(error, str(caught.exception))
        fleet_graph.drop(state, "s2")
        self.assertEqual(state["shape"], "diamond")
        fleet_graph.drop(state, "s3")  # two left: no longer a diamond
        self.assertEqual(state["shape"], "single-loop")
        self.assertIn("2 slices", state["shape_note"])
        fleet_graph.drop(state, "s4")
        with self.assertRaises(fleet.RunError) as caught:
            fleet_graph.drop(state, "s1")
        self.assertIn("only slice left", str(caught.exception))
        self.assertEqual(state["nodes"]["architect"]["result"]["slices"][1]["id"], "s2")  # its plan is untouched
        fleet_graph.approve(state, "plan")
        self.assertEqual(fleet_graph.advance(state), ["builder.s1"])
        brief = fleet.brief(state, "builder.s1")
        self.assertIn("## Dropped from the plan", brief)
        self.assertNotIn("## The other slices", brief)

    def test_choose_the_shape(self):
        state = self.at_gate()
        fleet_graph.choose_shape(state, "single-loop")
        self.assertEqual((state["shape"], state["shape_note"]), ("single-loop", None))
        fleet_graph.choose_shape(state, "diamond")
        self.assertEqual(state["shape"], "diamond")
        with self.assertRaises(fleet.RunError):
            fleet_graph.choose_shape(state, "star")
        outside = self.at_gate(repo=False)
        fleet_graph.choose_shape(outside, "diamond")
        self.assertEqual(outside["shape"], "single-loop")
        self.assertIn("not in a git repository", outside["shape_note"])

    def test_current_runs(self):
        done = self.at_gate()
        done.update(status="done", updated=1000)
        live = self.at_gate()
        self.assertEqual([s["id"] for s in fleet.current([done, live])], [live["id"]])
        self.assertEqual(len(fleet.current([done, live], now=1000 + 3600)), 2)


class DaemonGateTest(StateDir):
    def setUp(self):
        super().setUp()
        self.d = daemon.Daemon(Registry(self.root / "state" / "sessions.json"), is_alive=lambda p, s: True)
        self.d.spawn = lambda cmd, **kw: None
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None
        self.told, self.pushed = [], []
        self.d.notifier = mock.Mock(tell=lambda *a: self.told.append(a))
        self.d.pusher = mock.Mock(fleet=lambda state: self.pushed.append(state["status"]))

    def test_answers_from_inside_an_agent_are_refused(self):
        state = self.at_gate()
        fleet.save(state)
        with mock.patch.object(self.d, "from_agent", lambda pid: "claude (pid 42)"):
            for cmd, extra in (("fleet-approve", {}), ("fleet-send-back", {"note": "x"}),
                               ("fleet-drop", {"slice": "s1"}), ("fleet-shape", {"shape": "single-loop"})):
                response = self.d.handle_fleet(cmd, {"cmd": cmd, "run": state["id"], **extra}, peer=42)
                self.assertFalse(response["ok"])
                self.assertIn("inside an agent (claude (pid 42))", response["error"])
            # Starting and looking are not answers.
            self.assertTrue(self.d.handle_fleet("fleet-list", {"cmd": "fleet-list"}, peer=42)["ok"])
        self.assertEqual(fleet.load(state["id"])["gate"], "plan")
        self.assertIn("fleet-approve", daemon.FLEET_GATE_COMMANDS)

    def test_gate_commands(self):
        state = self.at_gate()
        fleet.save(state)
        run = state["id"]
        response = self.d.handle({"cmd": "fleet-drop", "run": run, "slice": "s3"})
        self.assertEqual(response["run"]["dropped"], ["s3"])
        self.assertIn("parallel builders need 3", self.d.handle({"cmd": "fleet-shape", "run": run,
                                                                 "shape": "diamond"})["run"]["shape_note"])
        self.assertFalse(self.d.handle({"cmd": "fleet-drop", "run": run, "slice": "s9"})["ok"])
        response = self.d.handle({"cmd": "fleet-send-back", "run": run, "note": "Smaller, please"})
        self.assertTrue(response["ok"], response)
        state = fleet.load(run)
        self.assertEqual(state["nodes"]["architect.2"]["status"], "running")  # queued and started
        self.assertIn("Smaller, please", self.d.registry.sessions[state["nodes"]["architect.2"]["session"]]["task"])

    def test_told_once_per_gate_or_hold(self):
        state = self.at_gate()
        self.d.fleet_saved(state, "at the gate")
        self.d.fleet_saved(state, "still at the gate")
        self.assertEqual(self.pushed, ["at-gate"])
        self.assertIn("a plan is ready for you", self.told[0][0])
        fleet.hold(state, "builder.s1: its session crashed", by="builder.s1")
        self.d.fleet_saved(state, "held")
        self.assertEqual(self.pushed, ["at-gate", "held"])
        self.assertEqual(self.told[-1][2], "critical")
        state["status"] = "running"
        self.d.fleet_saved(state, "running again")
        self.assertEqual(len(self.pushed), 2)  # running is not news

    def test_the_snapshot_carries_the_current_runs(self):
        state = self.at_gate()
        fleet.save(state)
        self.assertEqual([s["id"] for s in fleet.current(fleet.runs())], [state["id"]])


class MessageTest(StateDir):
    def test_what_a_push_says_at_each_level(self):
        state = self.at_gate()
        title, body = remote.fleet_message(state, "minimal")
        self.assertTrue(title.endswith(": a plan is ready for you"))
        self.assertNotIn("Make search faster", body)
        self.assertIn("Make search faster", remote.fleet_message(state, "summary")[1])
        self.assertIn("diamond: Part 1; Part 2; Part 3", remote.fleet_message(state, "full")[1])
        state.update(status="held", gate=None, reason="builder.s1: its session crashed")
        self.assertIn("its session crashed", remote.fleet_message(state, "full")[1])
        self.assertNotIn("crashed", remote.fleet_message(state, "summary")[1])
        state.update(status="at-gate", gate="merge")
        self.assertIn("ready to merge", remote.fleet_message(state, "minimal")[0])
        self.assertIn("fleet", config.REMOTE_EVENTS)
        self.assertIn("fleet", remote.STYLE)


class FakeDaemon:
    def __init__(self, runs):
        self.runs, self.requests = runs, []

    def __call__(self, payload, timeout=1.0):
        self.requests.append(payload)
        run = self.runs.get(payload.get("run"))
        return {"ok": True, "run": run} if run else {"ok": True}


class TopFleetTest(StateDir):
    def setUp(self):
        super().setUp()
        self.run_state = self.at_gate()
        self.daemon = FakeDaemon({self.run_state["id"]: self.run_state})
        self.t = top.Top(request=self.daemon, stop=lambda s: None, wait=lambda s: True, now=lambda: 10_000,
                         agents=["claude"], background=lambda work: work())
        self.t.apply({"ok": True, "sessions": [], "queue": {"held": False, "busy": 0, "limit": 2, "tasks": []},
                      "away": None, "approvals": [], "fleets": [self.run_state]})

    def screen(self, width=40, height=30):
        return self.t.render(width, height).text()

    def test_the_tab_and_the_plan(self):
        self.assertIn("Fleets 1!", self.screen().splitlines()[0])  # needs you
        self.assertIn(" F 1! ", self.screen(30).splitlines()[0])  # shortened to fit the narrowest phone
        self.t.key("tab")
        self.t.key("tab")
        self.assertEqual(self.t.tab, "fleets")
        text = self.screen()
        self.assertIn("Make search faster", text)
        self.assertIn("plan gate", text)
        self.t.key("enter")
        text = self.screen(60, 60)
        for expected in ("waiting at the plan gate", "shape: diamond", "s1: Part 1", "files: part1.py",
                         "risk: high: small", "not doing: Rewriting the parser", "approve: Three parts"):
            self.assertIn(expected, text)
        self.assertIn(" y Approve… ", text)
        # A long plan scrolls instead of moving to another run.
        self.t.key("down")
        self.assertEqual((self.t.scroll, self.t.index["fleets"]), (1, 0))

    def test_answering_the_gate(self):
        run = self.run_state["id"]
        self.t.switch("fleets")
        self.t.key("y")
        self.assertTrue(self.t.detail)
        self.t.key("y")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "fleet-approve", "run": run, "gate": "plan"})
        self.t.key("b")
        for ch in "Fewer parts":
            self.t.key(ch)
        self.assertIn("What should change: Fewer parts", self.screen())
        self.t.key("enter")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "fleet-send-back", "run": run, "note": "Fewer parts"})
        self.t.key("d")
        self.t.key("2")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "fleet-drop", "run": run, "slice": "s2"})
        self.t.key("l")
        self.t.key("1")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "fleet-shape", "run": run, "shape": "single-loop"})
        self.t.key("x")
        self.t.key("y")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "fleet-cancel", "run": run})

    def test_updates_arrive_as_events(self):
        held = dict(self.run_state, status="held", gate=None, reason="builder.s1: its session crashed")
        self.t.apply({"event": "fleet", "run": held["id"], "status": "held", "state": held})
        self.t.switch("fleets")
        self.assertIn("its session crashed", self.screen())
        self.assertNotIn(" y Approve", self.screen())


if __name__ == "__main__":
    unittest.main()
