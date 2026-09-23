import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import usage

NOW = datetime(2026, 9, 23, 23, 30, tzinfo=timezone.utc)


def rec(limits, updated=NOW - timedelta(minutes=5), ready=True):
    return {"id": "claude", "name": "Claude Code", "ready": ready, "updatedAt": updated.isoformat(), "limits": limits}


def limit(label, percent, resets=NOW + timedelta(hours=1)):
    return {"label": label, "percent": percent, "resetsAt": resets.isoformat() if resets else None}


class BlockingTest(unittest.TestCase):
    def test_the_worst_limit_over_the_threshold(self):
        r = rec([limit("Session (5-hour)", 0.92), limit("Weekly (7-day)", 0.95), limit("Other", 0.2)])
        block = usage.blocking("claude", 0.9, now=NOW, rec=r)
        self.assertEqual((block["label"], block["percent"]), ("Weekly (7-day)", 0.95))

    def test_under_the_threshold_or_turned_off(self):
        r = rec([limit("Session (5-hour)", 0.89)])
        self.assertIsNone(usage.blocking("claude", 0.9, now=NOW, rec=r))
        self.assertIsNone(usage.blocking("claude", 0, now=NOW, rec=rec([limit("S", 1.0)])))

    def test_a_limit_past_its_reset_no_longer_counts(self):
        r = rec([limit("Session (5-hour)", 0.99, resets=NOW - timedelta(minutes=1))])
        self.assertIsNone(usage.blocking("claude", 0.9, now=NOW, rec=r))

    def test_missing_stale_or_unready_records_never_block(self):
        full = [limit("S", 1.0)]
        self.assertIsNone(usage.blocking("claude", 0.9, now=NOW, rec=rec(full, updated=NOW - timedelta(hours=2))))
        self.assertIsNone(usage.blocking("claude", 0.9, now=NOW, rec=rec(full, ready=False)))
        self.assertIsNone(usage.blocking("claude", 0.9, now=NOW, rec={"limits": full}))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
            self.assertIsNone(usage.blocking("claude", 0.9, now=NOW))

    def test_bad_values_are_skipped(self):
        r = rec([{"label": "x", "percent": "lots"}, {"label": "y"}, limit("S", 0.95, resets=None)])
        self.assertEqual(usage.blocking("claude", 0.9, now=NOW, rec=r)["label"], "S")

    def test_reads_the_omarchy_record(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
            folder = Path(tmp) / "omarchy" / "agents" / "usage"
            folder.mkdir(parents=True)
            (folder / "claude.json").write_text(json.dumps(rec([limit("Session (5-hour)", 0.92)])))
            self.assertEqual(usage.blocking("claude", 0.9, now=NOW)["percent"], 0.92)
            self.assertAlmostEqual(usage.age(usage.record("claude"), now=NOW), 300)

    def test_describe(self):
        block = {"name": "Claude Code", "label": "Session (5-hour)", "percent": 0.924, "resetsAt": None}
        self.assertEqual(usage.describe(block), "Claude Code's Session (5-hour) limit is at 92%")
        block["resetsAt"] = NOW.isoformat()
        self.assertRegex(usage.describe(block), r"at 92%, resets \d\d:\d\d$")


if __name__ == "__main__":
    unittest.main()
