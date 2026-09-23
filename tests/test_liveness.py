import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, hooks, procs
from omaorchestra.registry import Registry


class FakeProc:
    """A /proc tree with just enough of each stat file."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def add(self, pid, comm, ppid, start):
        d = Path(self.root, str(pid))
        d.mkdir()
        # starttime is field 22: state, ppid, 17 filler fields, then starttime
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} " + " ".join(["0"] * 17) + f" {start} 0 0\n")

    def cleanup(self):
        self.tmp.cleanup()


class ProcsTest(unittest.TestCase):
    def setUp(self):
        self.proc = FakeProc()
        self.p = self.proc.root

    def tearDown(self):
        self.proc.cleanup()

    def test_start_time_survives_awkward_names(self):
        self.proc.add(10, "we ird) name", 1, 555)
        self.assertEqual(procs.start_time(10, self.p), 555)
        self.assertEqual(procs.parent(10, self.p), ("we ird) name", 1))

    def test_gone_process(self):
        self.assertIsNone(procs.start_time(99, self.p))
        self.assertFalse(procs.is_alive(99, 1, self.p))

    def test_reused_pid_is_not_alive(self):
        self.proc.add(10, "claude", 1, 200)
        self.assertTrue(procs.is_alive(10, 200, self.p))
        self.assertFalse(procs.is_alive(10, 100, self.p))

    def test_prefers_claude_pid(self):
        self.proc.add(10, "claude", 1, 300)
        self.assertEqual(procs.agent_process({"CLAUDE_PID": "10"}, start=1, proc=self.p), (10, 300))

    def test_walks_ancestry_when_claude_pid_missing_or_dead(self):
        self.proc.add(10, "claude", 1, 300)
        self.proc.add(20, "sh", 10, 400)
        self.proc.add(30, "python3", 20, 500)
        for env in ({}, {"CLAUDE_PID": "77"}, {"CLAUDE_PID": "junk"}):
            self.assertEqual(procs.agent_process(env, start=30, proc=self.p), (10, 300), env)

    def test_no_agent_ancestor(self):
        self.proc.add(20, "sh", 1, 400)
        self.assertIsNone(procs.agent_process({}, start=20, proc=self.p))

    def test_real_proc(self):
        start = procs.start_time(os.getpid())
        self.assertIsNotNone(start)
        self.assertTrue(procs.is_alive(os.getpid(), start))


class PruneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Registry(Path(self.tmp.name) / "sessions.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_prunes_only_dead_sessions_with_a_process(self):
        r = self.registry
        r.update("alive", "claude", "working", pid=1, pid_start=10)
        r.update("dead", "claude", "working", pid=2, pid_start=20)
        r.update("unknown", "claude", "idle")
        removed = r.prune(lambda pid, start: pid == 1)
        self.assertEqual(removed, ["dead"])
        self.assertEqual(sorted(s["id"] for s in Registry(r.path).list()), ["alive", "unknown"])

    def test_new_process_replaces_old_identity(self):
        r = self.registry
        r.update("s", "claude", "idle", pid=1, pid_start=10)
        r.update("s", "claude", "working", pid=5, pid_start=50)  # e.g. claude --resume
        self.assertEqual((r.sessions["s"]["pid"], r.sessions["s"]["pid_start"]), (5, 50))

    def test_list_prunes(self):
        self.registry.update("dead", "claude", "working", pid=2, pid_start=20)
        d = daemon.Daemon(self.registry, is_alive=lambda pid, start: False)
        self.assertEqual(d.handle({"cmd": "list"})["sessions"], [])

    def test_update_request_carries_process(self):
        d = daemon.Daemon(self.registry)
        d.handle({"cmd": "update", "session_id": "s", "agent": "claude", "status": "idle", "pid": 3, "pid_start": 30})
        self.assertEqual(self.registry.sessions["s"]["pid"], 3)


class PruneTimerTest(unittest.TestCase):
    def test_prune_forever_prunes_on_its_interval(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmp:
            registry = Registry(Path(tmp) / "sessions.json")
            registry.update("dead", "claude", "working", pid=2, pid_start=20)
            d = daemon.Daemon(registry, is_alive=lambda pid, start: False)

            async def run():
                task = asyncio.create_task(daemon.prune_forever(d, interval=0.01))
                await asyncio.sleep(0.05)
                task.cancel()

            asyncio.run(run())
            self.assertEqual(registry.list(), [])


class HookProcessTest(unittest.TestCase):
    def test_includes_process_when_known(self):
        req = hooks.request_for({"hook_event_name": "Stop", "session_id": "s"}, (10, 300))
        self.assertEqual((req["pid"], req["pid_start"]), (10, 300))

    def test_omits_process_when_unknown(self):
        for proc in (None, (10, None)):
            req = hooks.request_for({"hook_event_name": "Stop", "session_id": "s"}, proc)
            self.assertNotIn("pid", req)


if __name__ == "__main__":
    unittest.main()
