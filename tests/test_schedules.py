import contextlib
import datetime
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, schedules
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry


def local(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi).timestamp()


# 2026-10-05 is a Monday.
MONDAY = (2026, 10, 5)


class WhenTest(unittest.TestCase):
    def test_parses_and_describes(self):
        for text, described in (("daily 09:00", "daily at 09:00"), ("weekdays 9:00", "weekdays at 09:00"),
                                ("weekends at 10:30", "weekends at 10:30"), ("mon,thu 18:30", "Mon, Thu at 18:30"),
                                ("Monday, Thurs 7:05", "Mon, Thu at 07:05"), ("every 6h", "every 6h"),
                                ("every 2 hours", "every 2h"), ("every 30m", "every 30m")):
            self.assertEqual(schedules.describe(schedules.parse_when(text)), described, text)

    def test_refuses_what_it_cannot_read(self):
        for text in ("monkey 9:00", "daily 25:00", "daily", "every 5m", "every 9 days", "soon", "", "mo 9:00"):
            with self.assertRaises(schedules.ScheduleError, msg=text):
                schedules.parse_when(text)

    def test_next_run_on_days(self):
        weekdays = schedules.parse_when("weekdays 09:00")
        self.assertEqual(schedules.next_run(weekdays, local(*MONDAY, 8, 0)), local(*MONDAY, 9, 0))
        self.assertEqual(schedules.next_run(weekdays, local(*MONDAY, 9, 0)), local(2026, 10, 6, 9, 0),
                         "strictly after: not the time it is now")
        self.assertEqual(schedules.next_run(weekdays, local(2026, 10, 9, 10, 0)), local(2026, 10, 12, 9, 0),
                         "Friday after nine: next Monday")
        sunday = schedules.parse_when("sun 07:00")
        self.assertEqual(schedules.next_run(sunday, local(*MONDAY, 8, 0)), local(2026, 10, 11, 7, 0))

    def test_next_run_every(self):
        every = schedules.parse_when("every 6h")
        start = local(*MONDAY, 8, 0)
        self.assertEqual(schedules.next_run(every, start, anchor=start), start + 6 * 3600)
        self.assertEqual(schedules.next_run(every, start + 13 * 3600, anchor=start), start + 18 * 3600,
                         "counted from when it was made, not from now")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = schedules.Schedules(Path(self.tmp.name) / "schedules.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_find_pause_and_persist(self):
        now = local(*MONDAY, 8, 0)
        item = self.s.add("weekdays 09:00", [{"task": "update deps", "cwd": self.tmp.name}], now=now)
        self.assertEqual(item["next"], local(*MONDAY, 9, 0))
        self.assertEqual(schedules.Schedules.title(item), "update deps")
        self.s.set_paused(item["id"][:6], True)
        self.assertEqual(self.s.due(local(*MONDAY, 9, 30)), [], "a paused schedule is never due")
        self.s.set_paused(item["id"], False, now=local(2026, 10, 7, 12, 0))
        self.assertEqual(self.s.find(item["id"])["next"], local(2026, 10, 8, 9, 0),
                         "resumed: from now on, not the times it was paused through")
        again = schedules.Schedules(self.s.path)
        self.assertEqual(len(again.items), 1)

    def test_refusals(self):
        with self.assertRaises(schedules.ScheduleError):
            self.s.add("daily 09:00", [{"task": " ", "cwd": self.tmp.name}])
        with self.assertRaises(schedules.ScheduleError):
            self.s.add("daily 09:00", [{"task": "x", "cwd": "/no/such/dir"}])
        with self.assertRaises(schedules.ScheduleError):
            self.s.find("nope")


class DaemonScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "none.toml")})
        self.env.start()
        settings = daemon.config.defaults()
        settings["tasks"]["max_parallel"] = 1
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        self.spawned = []
        self.d.spawn = lambda cmd, **kw: self.spawned.append(cmd)
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda *a: None
        self.events = []
        self.d.publish = self.events.append

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def item(self, task, **extra):
        return {"task": task, "cwd": self.tmp.name, "worktree": False, "agent_bin": "true",
                "path": "/usr/bin:/bin", **extra}

    def add(self, when="weekdays 09:00", items=None, now=None):
        with mock.patch("time.time", return_value=now or local(*MONDAY, 8, 0)):
            response = self.d.handle({"cmd": "schedule-add", "when": when,
                                      "items": items or [self.item("update deps")], "task": "update deps"})
        self.assertTrue(response["ok"], response)
        return response["schedule"]

    def started(self):
        return [cmd[-1] for cmd in self.spawned]

    def finish_all(self):
        for sid, s in list(self.d.registry.sessions.items()):
            if s["status"] != "idle":
                self.d.handle({"cmd": "update", "session_id": sid, "agent": "claude", "status": "idle"})

    def test_queues_when_its_time_comes_and_tracks_the_run(self):
        s = self.add()
        self.d.run_schedules(local(*MONDAY, 8, 59))
        self.assertEqual(self.started(), [], "not before its time")
        self.d.run_schedules(local(*MONDAY, 9, 0))
        self.assertEqual(self.started(), ["update deps"], "queued at its time and started in a free slot")
        s = self.d.schedules.find(s["id"])
        self.assertEqual(s["next"], local(2026, 10, 6, 9, 0))
        snap = self.d.handle({"cmd": "schedule-list"})["schedules"]["schedules"][0]
        self.assertEqual((snap["status"], snap["whenText"], snap["title"]), ("running", "weekdays at 09:00", "update deps"))
        self.finish_all()
        snap = self.d.handle({"cmd": "schedule-list"})["schedules"]["schedules"][0]
        self.assertEqual(snap["status"], "finished")
        self.assertTrue(any(e.get("event") == "schedules" for e in self.events), "subscribers hear about it")

    def test_missed_times_run_once(self):
        s = self.add()
        # The machine was off from Monday morning to Thursday noon.
        self.d.run_schedules(local(2026, 10, 8, 12, 0))
        self.assertEqual(self.started(), ["update deps"], "one run, not one per missed morning")
        self.assertEqual(self.d.schedules.find(s["id"])["next"], local(2026, 10, 9, 9, 0))

    def test_skips_while_its_last_run_is_still_going(self):
        s = self.add(when="every 30m")
        self.d.run_schedules(local(*MONDAY, 8, 30))
        self.assertEqual(self.started(), ["update deps"])
        self.d.run_schedules(local(*MONDAY, 9, 0))
        self.assertEqual(self.started(), ["update deps"], "still working: this time is skipped")
        self.assertEqual(self.d.schedules.find(s["id"])["last_note"], "skipped: the last run was still going")
        self.finish_all()
        self.d.run_schedules(local(*MONDAY, 9, 30))
        self.assertEqual(self.started(), ["update deps", "update deps"])

    def test_a_queued_run_counts_as_still_going(self):
        self.d.handle({"cmd": "queue-hold"})
        s = self.add(when="every 30m")
        self.d.run_schedules(local(*MONDAY, 8, 30))
        self.d.run_schedules(local(*MONDAY, 9, 0))
        self.assertEqual(len(self.d.queue.tasks), 1, "never piles up behind a held queue")
        self.assertEqual(self.d.queue.tasks[0]["schedule"], s["id"])
        snap = self.d.handle({"cmd": "schedule-list"})["schedules"]["schedules"][0]
        self.assertEqual(snap["status"], "queued")

    def test_turned_off_queues_nothing_until_turned_on(self):
        self.add()
        self.d.settings["schedules"]["enabled"] = False
        self.d.run_schedules(local(*MONDAY, 9, 0))
        self.assertEqual(self.started(), [])
        self.d.settings["schedules"]["enabled"] = True
        self.d.run_schedules(local(*MONDAY, 10, 0))
        self.assertEqual(self.started(), ["update deps"], "the time missed meanwhile runs once")

    def test_run_by_hand_keeps_its_next_time(self):
        s = self.add()
        response = self.d.handle({"cmd": "schedule-run", "id": s["id"][:8]})
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.started(), ["update deps"])
        self.assertEqual(self.d.schedules.find(s["id"])["next"], local(*MONDAY, 9, 0))

    def test_a_recipe_queues_as_a_chain(self):
        self.d.handle({"cmd": "queue-hold"})
        self.add(items=[self.item("plan it", recipe="plan-then-build"),
                        self.item("build it", recipe="plan-then-build", same_worktree=True)])
        self.d.run_schedules(local(*MONDAY, 9, 0))
        first, second = self.d.queue.tasks
        self.assertEqual((first["state"], second["state"], second["after"]), ("pending", "waiting", first["id"]))

    def test_a_failed_launch_shows_as_its_status(self):
        self.add(items=[self.item("broken", agent_bin="/no/such/agent")])
        self.d.run_schedules(local(*MONDAY, 9, 0))
        snap = self.d.handle({"cmd": "schedule-list"})["schedules"]["schedules"][0]
        self.assertTrue(snap["status"].startswith("failed:"), snap["status"])

    def test_pause_remove_and_errors(self):
        s = self.add()
        self.d.handle({"cmd": "schedule-pause", "id": s["id"]})
        self.d.run_schedules(local(*MONDAY, 9, 0))
        self.assertEqual(self.started(), [])
        self.assertTrue(self.d.handle({"cmd": "schedule-remove", "id": s["id"]})["ok"])
        self.assertEqual(self.d.schedules.items, [])
        self.assertFalse(self.d.handle({"cmd": "schedule-remove", "id": "nope"})["ok"])
        bad = self.d.handle({"cmd": "schedule-add", "when": "whenever", "items": [self.item("x")]})
        self.assertFalse(bad["ok"])
        self.assertIn("say when", bad["error"])

    def test_survives_a_restart(self):
        self.add()
        again = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"))
        self.assertEqual([s["task"] for s in again.schedules.items], ["update deps"])


class ScheduleCliTest(unittest.TestCase):
    def test_add_sends_the_task_and_when(self):
        sent = []

        def fake(payload, timeout=1.0):
            sent.append(payload)
            schedule = {"id": "abcdef123", "when": schedules.parse_when(payload["when"])}
            return {"ok": True, "schedule": schedule, "schedules": {"enabled": True, "schedules": []}}
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("omaorchestra.client.request", fake), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["schedule", "add", "update deps", "--when", "weekdays 09:00", "--in", tmp,
                                   "--no-worktree", "--", "--verbose"]), 0)
        (item,) = sent[0]["items"]
        self.assertEqual((sent[0]["when"], item["task"], item["worktree"], item["extra"]),
                         ("weekdays 09:00", "update deps", False, ["--verbose"]))
        self.assertEqual(item["path"], os.environ["PATH"])
        self.assertIn("scheduled abcdef12: weekdays at 09:00", out.getvalue())

    def test_a_bad_time_is_refused_before_the_daemon(self):
        err = io.StringIO()
        with mock.patch("omaorchestra.client.request") as request, contextlib.redirect_stderr(err):
            self.assertEqual(main(["schedule", "add", "x", "--when", "sometime"]), 1)
        request.assert_not_called()
        self.assertIn("say when", err.getvalue())


if __name__ == "__main__":
    unittest.main()
