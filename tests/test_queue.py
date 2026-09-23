import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, taskqueue
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry


class TaskQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = taskqueue.TaskQueue(Path(self.tmp.name) / "queue.json")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, task, **kw):
        return self.q.add({"task": task, "cwd": self.tmp.name}, **kw)

    def test_order_states_and_persistence(self):
        a, b, c = self.add("a"), self.add("b", paused=True), self.add("c")
        self.assertEqual(self.q.next_pending()["task"], "a")
        self.q.fail(a, "boom")
        self.assertEqual(self.q.next_pending()["task"], "c", "failed and paused tasks are skipped")
        self.q.set_state(b["id"][:6], "pending")
        self.q.move(c["id"], 0)
        self.q.move(b["id"], 99)
        reloaded = taskqueue.TaskQueue(self.q.path)
        self.assertEqual([t["task"] for t in reloaded.tasks], ["c", "a", "b"])
        self.assertEqual((reloaded.tasks[1]["state"], reloaded.tasks[1]["error"]), ("failed", "boom"))
        self.q.set_state(a["id"], "pending")
        self.assertIsNone(self.q.find(a["id"])["error"], "retrying clears the error")

    def test_refusals(self):
        with self.assertRaises(taskqueue.QueueError):
            self.q.add({"task": " ", "cwd": self.tmp.name})
        with self.assertRaises(taskqueue.QueueError):
            self.q.add({"task": "x", "cwd": "/no/such/dir"})
        with self.assertRaises(taskqueue.QueueError):
            self.q.find("nope")
        with self.assertRaises(taskqueue.QueueError):
            self.q.set_state(self.add("x")["id"], "running")


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "none.toml")})
        self.env.start()
        settings = daemon.config.defaults()
        settings["tasks"]["max_parallel"] = 1
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        self.spawned = []
        self.d.spawn = lambda cmd, **kw: self.spawned.append((cmd, kw.get("env")))
        # Never the real usage records (they may well be near a limit).
        self.limit = None
        self.d.usage_check = lambda agent, threshold: self.limit
        self.refreshes = []
        self.d.usage_refresh = self.refreshes.append

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def queue(self, task, **extra):
        item = {"task": task, "cwd": self.tmp.name, "worktree": False, "agent_bin": "true",
                "path": "/usr/bin:/bin", **extra}
        return self.d.handle({"cmd": "queue-add", "item": item})

    def tasks(self):
        return [t["task"] for t in self.d.queue.tasks]

    def started(self):
        return [cmd[-1] for cmd, env in self.spawned]

    def finish_all(self):
        for sid, s in list(self.d.registry.sessions.items()):
            if s["status"] != "idle":
                self.d.handle({"cmd": "update", "session_id": sid, "agent": "claude", "status": "idle"})

    def test_starts_in_order_as_slots_free(self):
        self.queue("first")
        self.queue("second")
        self.assertEqual(self.started(), ["first"], "one slot: only the first starts")
        self.assertEqual(self.tasks(), ["second"])
        self.finish_all()
        self.assertEqual(self.started(), ["first", "second"], "a finished agent frees its slot")
        self.assertEqual(self.tasks(), [])
        cmd, env = self.spawned[0]
        self.assertEqual(env["PATH"], "/usr/bin:/bin", "launched with the PATH it was queued with")
        self.assertEqual(cmd[cmd.index("-e") + 1], "true", "launched with the agent it was queued with")

    def test_waiting_agents_hold_their_slot(self):
        self.queue("first")
        (sid,) = self.d.registry.sessions
        self.d.handle({"cmd": "update", "session_id": sid, "agent": "claude", "status": "needs-input"})
        self.queue("second")
        self.assertEqual(self.started(), ["first"])

    def test_hold_pause_and_run_now(self):
        self.d.handle({"cmd": "queue-hold"})
        a = self.queue("a")["item"]
        self.queue("b")
        self.assertEqual(self.started(), [], "a held queue starts nothing")
        self.d.handle({"cmd": "queue-pause", "id": a["id"]})
        self.d.handle({"cmd": "queue-release"})
        self.assertEqual(self.started(), ["b"], "paused tasks are skipped")
        response = self.d.handle({"cmd": "queue-run", "id": a["id"][:8]})
        self.assertTrue(response["ok"])
        self.assertEqual(self.started(), ["b", "a"], "run starts it whatever the limit")
        self.assertEqual(self.tasks(), [])

    def test_failed_launch_stays_queued_and_the_next_goes(self):
        self.queue("broken", agent_bin="/no/such/agent")
        self.queue("fine")
        self.assertEqual(self.started(), ["fine"])
        (failed,) = self.d.queue.tasks
        self.assertEqual(failed["state"], "failed")
        self.assertIn("not installed", failed["error"])

    def test_reload_with_more_slots_starts_more(self):
        for t in ("a", "b", "c"):
            self.queue(t)
        self.assertEqual(self.started(), ["a"])
        Path(os.environ["OMAORCHESTRA_CONFIG"]).write_text("[tasks]\nmax_parallel = 3\n")
        self.d.handle({"cmd": "reload"})
        self.assertEqual(self.started(), ["a", "b", "c"])

    def test_usage_limit_holds_the_queue_until_it_clears(self):
        self.limit = {"agent": "claude", "name": "Claude Code", "label": "Session (5-hour)", "percent": 0.93,
                      "resetsAt": None}
        self.queue("a")
        self.assertEqual(self.started(), [], "a nearly used limit holds the queue")
        snap = self.d.handle({"cmd": "queue-list"})["queue"]
        self.assertEqual(snap["blocked"]["text"], "Claude Code's Session (5-hour) limit is at 93%")
        response = self.d.handle({"cmd": "queue-run", "id": self.d.queue.tasks[0]["id"]})
        self.assertTrue(response["ok"], "run now overrides the limit")
        self.queue("b")
        self.finish_all()
        self.assertEqual(self.started(), ["a"])
        self.limit = None
        self.d.dispatch()  # what the periodic check does
        self.assertEqual(self.started(), ["a", "b"])
        self.assertIsNone(self.d.handle({"cmd": "queue-list"})["queue"]["blocked"])

    def test_queue_survives_a_restart(self):
        self.d.handle({"cmd": "queue-hold"})
        self.queue("later")
        again = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"))
        self.assertEqual(([t["task"] for t in again.queue.tasks], again.queue.held), (["later"], True))

    def test_errors_are_reported(self):
        self.assertFalse(self.d.handle({"cmd": "queue-cancel", "id": "nope"})["ok"])
        self.assertFalse(self.d.handle({"cmd": "queue-add", "item": {"task": "", "cwd": self.tmp.name}})["ok"])


class QueueCliTest(unittest.TestCase):
    def test_add_sends_the_environment_and_agent_args(self):
        sent = []

        def fake(payload, timeout=1.0):
            sent.append(payload)
            return {"ok": True, "item": {"id": "abcdef123"}, "queue": {"held": False, "busy": 0, "limit": 2, "tasks": []}}
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch("omaorchestra.client.request", fake), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["queue", "add", "do it", "--in", tmp, "--no-worktree", "--paused",
                                   "--", "--verbose"]), 0)
        item = sent[0]["item"]
        self.assertEqual((item["task"], item["worktree"], item["extra"], sent[0]["paused"]), ("do it", False, ["--verbose"], True))
        self.assertEqual(item["path"], os.environ["PATH"])
        self.assertIn("queued abcdef12", out.getvalue())


if __name__ == "__main__":
    unittest.main()
