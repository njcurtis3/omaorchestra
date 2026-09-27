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
from omaorchestra import fleet
from omaorchestra.app import present_fleet

try:
    from qt_app import application
    from PySide6.QtCore import QModelIndex  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.ids = fleet_fixtures.write_all(self.tmp.name, now=1_800_000_000)
        self.states = {name: fleet.load(run_id) for name, run_id in self.ids.items()}

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class RowsTest(FixtureCase):
    def test_groups_and_order(self):
        rows = present_fleet.rows(self.states.values(), now=1_800_000_000)
        groups = [r["group"] for r in rows]
        self.assertEqual(groups, sorted(groups, key=present_fleet.GROUPS.index))
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[self.ids["running"]]["group"], "needs-you")  # a node waits for you
        self.assertEqual(by_id[self.ids["running"]]["statusText"], "Running: builder.s2 waits for you")
        self.assertEqual(by_id[self.ids["plan"]]["statusText"], "Waiting at the plan gate")
        self.assertEqual(by_id[self.ids["done"]]["group"], "finished")
        self.assertEqual([d["state"] for d in by_id[self.ids["running"]]["dots"]],
                         ["passed", "passed", "passed", "passed", "you"])
        self.assertEqual(by_id[self.ids["plan"]]["budget"], 5)
        self.assertAlmostEqual(by_id[self.ids["running"]]["spent"], 2.97)


class CardsTest(FixtureCase):
    def card(self, name):
        cards = present_fleet.cards(self.states[name])
        self.assertEqual(len(cards), 1)
        return cards[0]

    def test_a_card_for_each_decision(self):
        plan = self.card("plan")
        self.assertEqual((plan["kind"], plan["shape"], [s["id"] for s in plan["slices"]]),
                         ("plan", "diamond", ["s1", "s2", "s3"]))
        self.assertEqual(plan["notDoing"], ["Changing the public API", "Styling"])
        self.assertTrue(plan["planKiller"])
        self.assertIn("approve", plan["actions"])
        merge = self.card("merge")
        self.assertEqual((merge["kind"], len(merge["slices"])), ("merge", 3))
        scope = self.card("scope")
        self.assertEqual((scope["kind"], scope["node"], scope["files"]), ("scope", "builder.s1",
                                                                          ["CHANGELOG.md", "setup.cfg"]))
        rejected = self.card("rejected")
        self.assertEqual((rejected["kind"], [r["attempt"] for r in rejected["reviews"]]), ("rejected", [1, 2]))
        self.assertIn("take-over", rejected["actions"])
        self.assertEqual(self.card("bad-reply")["kind"], "bad-reply")
        self.assertEqual(self.card("done")["kind"], "close")
        self.assertEqual(present_fleet.cards(self.states["running"]), [])
        paused = dict(self.states["running"], status="held", held_by="paused", reason="paused by you")
        self.assertEqual(present_fleet.cards(paused)[0]["kind"], "paused")
        limits = dict(self.states["running"], status="held", held_by="limits", reason="the run has spent $6.00")
        self.assertEqual(present_fleet.cards(limits)[0]["kind"], "limits")


class GraphTest(FixtureCase):
    def test_a_diamond(self):
        g = present_fleet.graph(self.states["merge"])
        self.assertEqual((g["columns"], g["lanes"]), (7, 3))
        boxes = {n["key"]: n for n in g["nodes"]}
        self.assertEqual((boxes["scout"]["lane"], boxes["builder.s3"]["lane"]), (1, 2))
        self.assertEqual(boxes["merge-gate"]["state"], "you")
        self.assertEqual(boxes["builder.s2"]["node"], "builder.s2.2")  # its latest attempt
        self.assertEqual(boxes["integrator"]["state"], "queued")
        back = [e for e in g["edges"] if e["kind"] == "back"]
        self.assertEqual(back, [{"from": "reviewer.s2", "to": "builder.s2", "kind": "back", "label": "try 2/2"}])
        self.assertEqual(sum(1 for e in g["edges"] if e["to"] == "merge-gate"), 3)

    def test_a_single_loop_and_before_the_plan(self):
        g = present_fleet.graph(self.states["done"])
        self.assertEqual(g["columns"], 5)
        self.assertEqual([(e["from"], e["to"]) for e in g["edges"] if e["kind"] == "next"],
                         [("reviewer.s1", "builder.s2"), ("reviewer.s2", "builder.s3")])
        early = present_fleet.graph(self.states["bad-reply"])
        self.assertEqual([n["key"] for n in early["nodes"]], ["scout", "architect", "plan-gate"])
        self.assertEqual(early["nodes"][0]["state"], "held")


class TimelineTest(FixtureCase):
    def test_lanes(self):
        state = self.states["running"]
        t = present_fleet.timeline(state, fleet.read_activity(state["id"]), now=state["updated"] + 600)
        lanes = {lane["node"]: lane for lane in t["lanes"]}
        self.assertEqual(set(lanes), set(state["nodes"]))
        self.assertEqual([s["kind"] for s in lanes["builder.s2"]["segments"]], ["working", "waiting"])
        self.assertGreater(lanes["builder.s2"]["waited"], 0)
        for lane in t["lanes"]:
            for seg in lane["segments"]:
                self.assertTrue(0 <= seg["x"] <= 1 and 0 < seg["w"] <= 1)
        self.assertEqual(t["gates"][0]["gate"], "plan")


class NodeDetailTest(FixtureCase):
    def test_a_builder_and_a_reviewer(self):
        d = present_fleet.node_detail(self.states["scope"], "builder.s1")
        self.assertEqual((d["role"], d["extra"], d["accepted"]), ("builder", ["CHANGELOG.md", "setup.cfg"], ""))
        self.assertIn("## Your slice", d["brief"])
        self.assertIn(("done when", "python -m pytest tests/test_login.py (passed)"),
                      [(r["label"], r["text"]) for r in d["result"]])
        r = present_fleet.node_detail(self.states["rejected"], "reviewer.s1.2")
        self.assertIn(("verdict", "REJECT"), [(x["label"], x["text"]) for x in r["result"]])

    def test_the_stop_rule_hint(self):
        self.assertIn("single agent", present_fleet.stop_rule_hint("Fix the typo"))
        self.assertEqual(present_fleet.stop_rule_hint(""), "")
        self.assertEqual(present_fleet.stop_rule_hint(" ".join(["word"] * 20)), "")


class SummaryTest(FixtureCase):
    def test_fleets_json_for_the_bar(self):
        out = fleet.write_summary()
        on_disk = json.loads(fleet.summary_path().read_text())
        self.assertEqual(out, on_disk)
        by_id = {r["id"]: r for r in on_disk}
        self.assertEqual(set(by_id), set(self.ids.values()))  # the done run is not closed yet
        self.assertTrue(by_id[self.ids["plan"]]["needsYou"])
        self.assertTrue(by_id[self.ids["running"]]["needsYou"])
        self.assertFalse(by_id[self.ids["done"]]["needsYou"])


class QueueRowsTest(unittest.TestCase):
    def test_a_runs_nodes_are_one_row(self):
        if not HAVE_QT:
            self.skipTest("PySide6 not available to this Python")
        from omaorchestra.app.backend import Queue
        rows = Queue._grouped([{"id": "a", "task": "x"}, {"id": "b", "fleet": "r1", "node": "builder.s1"},
                               {"id": "c", "task": "y"}, {"id": "d", "fleet": "r1", "node": "reviewer.s2"}])
        self.assertEqual([(r["id"], r["index"]) for r in rows], [("a", 0), ("fleet:r1", 1), ("c", 2)])
        self.assertEqual(rows[1]["nodes"], ["builder.s1", "reviewer.s2"])


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class KeyedListModelTest(unittest.TestCase):
    def test_rows_change_in_place(self):
        application()
        from omaorchestra.app.backend import KeyedListModel
        model = KeyedListModel("id")
        seen = []
        model.rowsInserted.connect(lambda *a: seen.append("insert"))
        model.rowsRemoved.connect(lambda *a: seen.append("remove"))
        model.rowsMoved.connect(lambda *a: seen.append("move"))
        model.dataChanged.connect(lambda *a: seen.append("change"))
        model.modelReset.connect(lambda *a: seen.append("reset"))
        model.set_items([{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}])
        self.assertEqual((model.rowCount(), seen), (3, ["insert"] * 3))
        seen.clear()
        model.set_items([{"id": "c", "v": 1}, {"id": "a", "v": 2}])
        self.assertEqual([i["id"] for i in model.items()], ["c", "a"])
        self.assertEqual(seen, ["remove", "move", "change"])  # never a reset
        seen.clear()
        model.set_items([{"id": "c", "v": 1}, {"id": "a", "v": 2}])
        self.assertEqual(seen, [])
        self.assertEqual(model.get(1), {"id": "a", "v": 2})
        self.assertEqual(model.get(9), {})
        self.assertEqual(model.data(model.index(0), KeyedListModel.KEY), "c")


if __name__ == "__main__":
    unittest.main()
