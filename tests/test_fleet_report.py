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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fleet_fixtures
from omaorchestra import client, fleet, fleet_report
from omaorchestra.__main__ import main


class SegmentsTest(unittest.TestCase):
    def test_stretches_of_working_and_waiting(self):
        activity = [{"at": 0, "event": "started", "node": "b"}, {"at": 5, "event": "working", "node": "b"},
                    {"at": 10, "event": "needs-input", "node": "b"}, {"at": 30, "event": "working", "node": "b"},
                    {"at": 40, "event": "idle", "node": "b"}, {"at": 50, "event": "result", "node": "b"},
                    {"at": 60, "event": "started", "node": "c"}, {"event": "held", "at": 61},
                    {"at": "junk", "event": "working", "node": "c"}]
        self.assertEqual(fleet_report.segments(activity, end=100), {
            "b": [("working", 0, 10), ("waiting", 10, 30), ("working", 30, 40), ("idle", 40, 50)],
            "c": [("working", 60, 100)]})

    def test_overlap(self):
        self.assertEqual(fleet_report._overlap([(0, 10), (5, 15), (20, 30)]), (2, 5 / 25))
        self.assertEqual(fleet_report._overlap([(0, 10), (10, 20)]), (1, 0.0))  # one after the other
        self.assertEqual(fleet_report._overlap([]), (0, 0.0))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.ids = fleet_fixtures.write_all(self.tmp.name, now=1_800_000_000)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def report(self, name, now=1_800_000_000):
        state = fleet.load(self.ids[name])
        return fleet_report.report(state, fleet.read_activity(state["id"]), now=now)

    def test_a_diamond_at_its_merge_gate(self):
        r = self.report("merge")
        roles = {x["role"]: x for x in r["roles"]}
        self.assertEqual({k: roles[k]["nodes"] for k in roles},
                         {"scout": 1, "architect": 1, "builder": 4, "reviewer": 4})
        self.assertAlmostEqual(roles["builder"]["cost"], 3.3)  # the retried s2 build cost nothing extra here
        self.assertEqual(r["roles"][0]["role"], "builder")  # the most expensive first
        self.assertAlmostEqual(sum(x["share"] for x in r["roles"]), 1.0, places=2)
        slices = {s["slice"]: s for s in r["slices"]}
        self.assertEqual((slices["s2"]["builds"], slices["s2"]["rejects"], slices["s2"]["verdict"]), (2, 1, "PASS"))
        self.assertEqual((r["rejects"], r["sent_back"]), (1, 1))
        self.assertEqual([g["gate"] for g in r["gates"]], ["plan", "merge"])
        self.assertGreater(r["gates"][0]["waited_s"], 0)
        self.assertEqual(r["parallel"]["builders_at_once"], 1)  # the fixture builds one after another
        self.assertTrue(r["estimated"])
        builder = next(n for n in r["nodes"] if n["id"] == "builder.s1")
        self.assertEqual(builder["working_s"], 10 * 60)

    def test_waiting_and_holds(self):
        running = self.report("running")
        builder = next(n for n in running["nodes"] if n["id"] == "builder.s2")
        self.assertEqual(builder["waits"], 1)
        self.assertGreater(builder["waiting_s"], 0)
        scope = self.report("scope")
        self.assertEqual(scope["holds"]["count"], 1)
        self.assertIn("changed files outside its slice", scope["holds"]["reasons"][0])
        self.assertEqual(scope["slices"][0]["extra_files"], 2)

    def test_stats_across_runs(self):
        s = fleet_report.stats(fleet.runs())
        self.assertEqual(s["runs"], len(self.ids))
        roles = {r["role"]: r for r in s["roles"]}
        self.assertEqual(roles["reviewer"]["rejected"], 3)  # s2 in the diamond, and s1 rejected twice
        self.assertAlmostEqual(s["reject_rate"], round(3 / roles["reviewer"]["done"], 3))
        self.assertGreater(roles["builder"]["working_s"], 0)
        self.assertEqual(fleet_report.stats(fleet.runs(), since=2_000_000_000)["runs"], 0)
        self.assertEqual(fleet_report.stats([{**fleet.runs()[0], "outside": "graph_agents"}])["runs"], 0)

    def test_the_command_line(self):
        runs = {run["id"]: run for run in fleet.runs()}

        def request(payload, timeout=None):
            if payload["cmd"] == "fleet-show":
                return {"ok": True, "run": runs[payload["run"]], "activity": fleet.read_activity(payload["run"])}
            return {"ok": True, "runs": list(runs.values())}
        out = io.StringIO()
        with mock.patch.object(client, "request", request), contextlib.redirect_stdout(out):
            self.assertEqual(main(["fleet", "report", self.ids["merge"]]), 0)
            self.assertEqual(main(["fleet", "stats", "--days", "0"]), 0)
            self.assertEqual(main(["fleet", "report", self.ids["merge"], "--json"]), 0)
        text = out.getvalue()
        self.assertIn("builder      4", text)
        self.assertIn("s2      2       1        PASS", text)
        self.assertIn("the merge gate waited", text)
        self.assertIn(f"{len(self.ids)} run(s)", text)
        self.assertIn('"builders_at_once": 1', text)


if __name__ == "__main__":
    unittest.main()
