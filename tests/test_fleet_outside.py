import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import client, config, fleet, fleet_outside
from omaorchestra.__main__ import main
from omaorchestra.app import present_fleet

# A graph_agents run as its fleet writes one (fields from its _schema.json).
DONE_RUN = {
    "run_id": "2026-09-20-fleet-gaps", "goal": "Close three gaps", "app": "graph_agents", "status": "done",
    "approved_by_human": True,
    "scout": {"written_by": "scout", "facts": ["postmortem.py:68 reads the lanes"], "unknowns": [], "risks": ["r"]},
    "architect": {"written_by": "architect", "shape": "diamond", "rationale": "three disjoint parts",
                  "plan": [{"slice": "s1", "intent": "Diagnose", "files": ["a.py"], "done_when": "t"},
                           {"slice": "s2", "intent": "Split", "files": ["b.py"], "done_when": "t", "risk": "high"},
                           {"slice": "s3", "intent": "Book it", "files": ["c.md"], "done_when": "t"}],
                  "edges": "s1, s2, s3 all parallel", "human_gate": "three slices", "not_doing": ["no deploys"]},
    "builders": {"s1": {"written_by": "builder", "status": "done", "branch": "s1-x", "changed": ["a.py"]},
                 "s2": {"written_by": "builder", "status": "done", "branch": "s2-x", "changed": ["b.py"]},
                 "s3": {"written_by": "builder", "status": "pending|done|failed", "branch": "", "changed": []}},
    "reviews": {"s1": {"written_by": "reviewer", "verdict": "PASS", "attempt": 1, "summary": "fine", "findings": []},
                "s2": {"written_by": "reviewer", "verdict": "REJECT", "attempt": 1, "summary": "no",
                       "findings": [{"severity": "blocker", "where": "b.py:3", "what": "wrong"}, "a plain note"],
                       "attempt_2": {"written_by": "reviewer", "verdict": "PASS", "summary": "now fine", "findings": []}},
                "s3": {"written_by": "reviewer", "verdict": "PASS|REJECT"}},
    "integrator": {"written_by": "integrator", "merged": [{"slice": "s1"}, {"slice": "s2"}],
                   "verification": {"command": "pytest", "output": "12 passed"}},
}
# One from before graph_agents stamped written_by, parked before approval.
PARKED_RUN = {"run_id": "2026-08-25-refuge-freshness", "goal": "Freshness", "status": "parked",
              "parked_note": "waiting on the API", "approved_by_human": False,
              "scout": {"facts": ["a fact"]},
              "architect": {"shape": "single-loop", "plan": [{"slice": "s1", "intent": "x", "files": ["y"]}]}}
ACTIVITY = [{"t": 100.0, "ev": "start", "agent": "scout", "id": "a3881aaaa"},
            {"t": 110.0, "ev": "tool", "agent": "scout", "tool": "Read"},
            {"t": 160.0, "ev": "stop", "agent": "scout", "id": "a3881aaaa"},
            {"t": 170.0, "ev": "stop", "agent": "builder", "id": "phantom"},  # a stop with no start
            {"t": 200.0, "ev": "start", "agent": "builder", "id": "b77aaaa"}]


class OutsideCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.umbrella = self.root / "repos"
        runs = self.umbrella / "graph_agents" / ".graph" / "runs"
        for name, state in (("2026-09-20-fleet-gaps", DONE_RUN), ("2026-08-25-refuge-freshness", PARKED_RUN)):
            (runs / name).mkdir(parents=True)
            (runs / name / "state.json").write_text(json.dumps(state))
        (runs / "2026-09-20-fleet-gaps" / "activity.jsonl").write_text(
            "\n".join(json.dumps(e) for e in ACTIVITY) + "\nnot json\n")
        (runs / "broken").mkdir()
        (runs / "broken" / "state.json").write_text("{ nope")
        (runs / "_schema.json").write_text("{}")
        self.claude = self.root / "claude"
        team = self.claude / "teams" / "session-1234abcd"
        team.mkdir(parents=True)
        (team / "config.json").write_text(json.dumps({"members": [
            {"name": "team-lead", "agentId": "x", "agentType": "team-lead"},
            {"name": "researcher", "agentId": "y", "agentType": "scout"}, "junk"]}))
        tasks = self.claude / "tasks" / "session-1234abcd"
        tasks.mkdir(parents=True)
        (tasks / "1.json").write_text(json.dumps({"id": "1", "subject": "Read the docs", "status": "completed",
                                                  "owner": "researcher"}))
        (tasks / "2.json").write_text(json.dumps({"id": "2", "subject": "Write it up", "status": "pending",
                                                  "blockedBy": ["1"]}))
        (tasks / "3.json").write_text("not json")
        self.settings = {"watch": [str(self.umbrella)], "agent_teams": True}
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude),
                                                "OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class ReadTest(OutsideCase):
    def test_a_graph_agents_run(self):
        run = fleet_outside.graph_agents_run(self.umbrella / "graph_agents/.graph/runs/2026-09-20-fleet-gaps/state.json")
        self.assertEqual((run["id"], run["outside"], run["status"], run["shape"]),
                         ("graph_agents:2026-09-20-fleet-gaps", "graph_agents", "done", "diamond"))
        self.assertEqual(run["approved"], {"plan": {"at": None}})
        self.assertEqual(set(run["nodes"]), {"scout", "architect", "builder.s1", "builder.s2", "builder.s3",
                                             "reviewer.s1", "reviewer.s2", "reviewer.s2.2", "integrator"})
        self.assertEqual(run["nodes"]["builder.s3"]["status"], "done")  # a placeholder status, in a finished run
        self.assertEqual(run["nodes"]["reviewer.s2"]["result"]["findings"],
                         [{"severity": "blocker", "where": "b.py:3", "what": "wrong"},
                          {"severity": "note", "where": "", "what": "a plain note"}])
        self.assertEqual(run["nodes"]["integrator"]["result"]["merged"], ["s1", "s2"])
        board = {r["slice"]: r for r in fleet.board(run)}
        self.assertEqual((board["s2"]["verdict"], board["s3"]["verdict"]), ("PASS", None))  # s3's is a placeholder
        self.assertEqual(board["s2"]["risk"], "high")
        self.assertEqual(board["s1"]["branch"], None)  # its own branch is on the builder node, not a worktree
        self.assertEqual(run["nodes"]["builder.s1"]["git"], {"branch": "s1-x"})

    def test_an_old_parked_run(self):
        run = fleet_outside.graph_agents_run(
            self.umbrella / "graph_agents/.graph/runs/2026-08-25-refuge-freshness/state.json")
        self.assertEqual((run["status"], run["reason"], run["approved"]), ("held", "waiting on the API", {}))
        self.assertEqual(set(run["nodes"]), {"scout", "architect"})  # no written_by, but content
        self.assertEqual([r["slice"] for r in fleet.board(run)], ["s1"])

    def test_activity_lanes(self):
        events = fleet_outside.activity(self.umbrella / "graph_agents/.graph/runs/2026-09-20-fleet-gaps/activity.jsonl")
        self.assertEqual(events, [{"at": 100.0, "event": "started", "node": "scout a3881"},
                                  {"at": 160.0, "event": "ended", "node": "scout a3881"},
                                  {"at": 200.0, "event": "started", "node": "builder b77aa"}])
        self.assertEqual(fleet_outside.activity(self.root / "missing.jsonl"), [])

    def test_agent_teams(self):
        (team,) = fleet_outside.team_runs()
        self.assertEqual((team["id"], team["outside"], list(team["nodes"])),
                         ("team:session-1234abcd", "agent-team", ["team-lead", "researcher"]))
        self.assertEqual(team["nodes"]["researcher"]["role"], "scout")
        self.assertEqual([(t["id"], t["status"], t["blockedBy"]) for t in team["tasks"]],
                         [("1", "completed", []), ("2", "pending", ["1"])])
        self.assertEqual(present_fleet.status_text(team), "2 teammates, 1 of 2 tasks done")
        self.assertEqual(present_fleet.team(team)["members"][0], {"name": "team-lead", "role": "team-lead"})
        self.assertEqual(fleet_outside.team_runs(self.root / "nowhere"), [])

    def test_all_of_them_and_find(self):
        found = fleet_outside.runs(self.settings)
        self.assertEqual(sorted(r["id"] for r in found), ["graph_agents:2026-08-25-refuge-freshness",
                                                          "graph_agents:2026-09-20-fleet-gaps",
                                                          "team:session-1234abcd"])
        self.assertEqual([r["id"] for r in fleet_outside.runs({"watch": [], "agent_teams": False})], [])
        self.assertEqual(fleet_outside.run_dirs([str(self.umbrella / "graph_agents"), str(self.umbrella)]),
                         [self.umbrella / "graph_agents" / ".graph" / "runs"])  # the checkout or its parent: once
        state, events = fleet_outside.find("graph_agents:2026-09-20", self.settings)
        self.assertEqual((state["goal"], len(events)), ("Close three gaps", 3))
        with self.assertRaises(KeyError):
            fleet_outside.find("graph_agents:", self.settings)  # two match
        with self.assertRaises(KeyError):
            fleet_outside.find("nothing", self.settings)


class ShownReadOnlyTest(OutsideCase):
    def test_omafleet_shows_but_never_answers_them(self):
        found = fleet_outside.runs(self.settings)
        rows = present_fleet.rows(found)
        self.assertEqual({r["group"] for r in rows}, {"outside"})
        self.assertEqual(rows[0]["groupLabel"], "Outside (read-only)")
        parked = next(s for s in found if s["status"] == "held")
        self.assertEqual(present_fleet.cards(parked), [])  # held, but not ours to answer
        self.assertFalse(fleet.needs_you(parked) and present_fleet.group(parked) == "needs-you")
        done = next(s for s in found if s["id"].endswith("fleet-gaps"))
        timeline = present_fleet.timeline(done, fleet_outside.find(done["id"], self.settings)[1], now=300)
        self.assertEqual([lane["node"] for lane in timeline["lanes"]], ["scout a3881", "builder b77aa"])
        self.assertIn("graph_agents run", present_fleet.node_detail(done, "builder.s1")["brief"])
        graph = present_fleet.graph(done)
        self.assertEqual(sum(1 for n in graph["nodes"] if n["key"].startswith("builder")), 3)

    def test_the_config(self):
        self.assertEqual(config.defaults()["fleets"], {"watch": [], "agent_teams": True})
        (self.root / "c.toml").write_text(f'[fleets]\nwatch = ["{self.umbrella}"]\n')
        self.assertEqual(config.load()["fleets"]["watch"], [str(self.umbrella)])
        (self.root / "c.toml").write_text('[fleets]\nwatch = [""]\n')
        with self.assertRaises(config.ConfigError):
            config.load()
        (self.root / "c.toml").write_text("")
        field = next(f for s in config.describe(config.defaults()) if s["section"] == "fleets" for f in s["fields"]
                     if f["key"] == "watch")
        self.assertEqual(field["kind"], "list")
        self.assertEqual(config.parse_value("fleets", "watch", "~/a, ~/b"), ["~/a", "~/b"])

    def test_the_command_line(self):
        (self.root / "c.toml").write_text(f'[fleets]\nwatch = ["{self.umbrella}"]\n')
        out = io.StringIO()
        with mock.patch.object(client, "request", lambda payload, timeout=None: {"ok": True, "runs": []}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["fleet", "list", "--outside"]), 0)
            self.assertEqual(main(["fleet", "show", "graph_agents:2026-09-20"]), 0)
            self.assertEqual(main(["fleet", "show", "team:session"]), 0)
        text = out.getvalue()
        self.assertIn("outside (read-only):", text)
        self.assertIn("graph_agents:2026-09-20-fleet-gaps", text)
        self.assertIn("read-only, from", text)
        self.assertIn("s2      done         PASS", text)
        self.assertIn("members: team-lead (team-lead), researcher (scout)", text)
        self.assertIn("[pending] Write it up", text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["fleet", "show", "graph_agents:nope"]), 1)
        self.assertIn("no outside run matching", err.getvalue())


if __name__ == "__main__":
    unittest.main()
