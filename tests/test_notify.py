import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, notify
from omaorchestra.registry import Registry

SETTINGS = {"waiting": True, "finished_after": 120}


def s(status, since=1000, **extra):
    return {"id": "s1", "status": status, "status_since": since, "cwd": "/w/proj", **extra}


class DecideTest(unittest.TestCase):
    def test_table(self):
        cases = [
            (None, s("needs-input"), "waiting"),
            (s("working"), s("needs-input"), "waiting"),
            (s("needs-input"), s("needs-input"), None),
            (s("needs-input"), s("working"), "clear"),
            (s("needs-input"), None, "clear"),
            (s("working", since=1000), s("idle"), "finished"),     # worked 200 s
            (s("working", since=1150), s("idle"), None),           # worked 50 s
            (s("idle"), s("working"), None),
            (None, s("idle"), None),
            (s("idle"), None, None),
        ]
        for previous, session, expected in cases:
            with self.subTest(previous=previous and previous["status"], session=session and session["status"]):
                self.assertEqual(notify.decide(previous, session, SETTINGS, now=1200), expected)

    def test_settings_turn_things_off(self):
        self.assertIsNone(notify.decide(None, s("needs-input"), {"waiting": False, "finished_after": 120}, now=1200))
        self.assertIsNone(notify.decide(s("working", since=0), s("idle"), {"waiting": True, "finished_after": 0}, now=9999))

    def test_content(self):
        self.assertEqual(notify.content("waiting", None, s("needs-input", message="Allow Bash?")),
                         ("proj needs you", "Allow Bash?", "normal"))
        self.assertEqual(notify.content("waiting", None, s("needs-input")), ("proj needs you", "Waiting for your input", "normal"))
        self.assertEqual(notify.content("finished", s("working", since=0), s("idle"), now=3900),
                         ("proj finished", "Worked for 1h 5m", "low"))


class FakeProc:
    def __init__(self, lines, release):
        self.lines = lines
        self.release = release
        self.stdout = self

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.release.wait()
        if not self.lines:
            raise StopAsyncIteration
        return self.lines.pop(0)

    async def wait(self):
        return 0


class NotifierTest(unittest.TestCase):
    def run_with(self, script, ids=(b"7\n",), after_id=()):
        """Run `script(notifier)` with fake notify-send and gdbus."""
        log = {"spawned": [], "closed": [], "focused": []}

        async def main():
            release = asyncio.Event()
            id_iter = iter(ids)

            async def spawn(*argv, **kw):
                log["spawned"].append(argv)
                return FakeProc([next(id_iter, b"8\n"), *after_id], release)

            async def close(nid):
                log["closed"].append(nid)

            async def focus(sid):
                log["focused"].append(sid)

            n = notify.Notifier(SETTINGS, focus=focus, spawn=spawn, close=close)
            await script(n)
            release.set()
            for _ in range(20):
                await asyncio.sleep(0)
            await asyncio.gather(*n.tasks)
            log["shown"] = n.shown

        asyncio.run(main())
        return log

    @staticmethod
    async def settle():
        for _ in range(10):
            await asyncio.sleep(0)

    def test_waiting_then_answered_closes_it(self):
        async def script(n):
            n.changed(s("working"), s("needs-input", message="Allow Bash?"))
            await self.settle()
            n.changed(s("needs-input"), s("working"))
            await self.settle()
        log = self.run_with(script)
        argv = log["spawned"][0]
        self.assertIn("--urgency=normal", argv)
        self.assertIn("--action=focus=Focus", argv)
        self.assertEqual(argv[-2:], ("proj needs you", "Allow Bash?"))
        self.assertEqual(log["closed"], [7])

    def test_answered_before_the_id_arrives(self):
        async def script(n):
            n.changed(s("working"), s("needs-input"))
            n.changed(s("needs-input"), s("working"))  # same tick: no id yet
            await self.settle()
        self.assertEqual(self.run_with(script)["closed"], [7])

    def test_focus_action(self):
        async def script(n):
            n.changed(None, s("needs-input"))
            await self.settle()
        self.assertEqual(self.run_with(script, after_id=(b"focus\n",))["focused"], ["s1"])

    def test_finished_survives_session_end(self):
        async def script(n):
            n.changed(s("working", since=0), s("idle"))
            await self.settle()
            n.changed(s("idle"), None)
            await self.settle()
        log = self.run_with(script)
        self.assertIn("--urgency=low", log["spawned"][0])
        self.assertEqual(log["closed"], [])

    def test_second_notification_replaces_the_first(self):
        async def script(n):
            n.changed(None, s("needs-input"))
            await self.settle()
            n.changed(s("working", since=0), s("idle"))
            await self.settle()
        log = self.run_with(script)
        self.assertIn("--replace-id=7", log["spawned"][1])

    def test_missing_notify_send_is_harmless(self):
        async def main():
            async def spawn(*a, **k):
                raise FileNotFoundError

            async def nothing(*a):
                pass

            n = notify.Notifier(SETTINGS, focus=nothing, spawn=spawn, close=nothing)
            n.changed(None, s("needs-input"))
            n.changed(None, s("needs-input") | {"id": "s2"})
            await asyncio.gather(*n.tasks)
            return n.warned
        self.assertTrue(asyncio.run(main()))


class DaemonWiringTest(unittest.TestCase):
    def test_daemon_reports_changes_and_endings(self):
        calls = []

        class Recorder:
            def changed(self, previous, session):
                calls.append((previous and previous["status"], session and session["status"]))

        with tempfile.TemporaryDirectory() as tmp:
            d = daemon.Daemon(Registry(Path(tmp) / "r.json"), is_alive=lambda p, st: False, notifier=Recorder())
            up = {"cmd": "update", "session_id": "s1", "agent": "claude"}
            d.handle({**up, "status": "working"})
            d.handle({**up, "status": "needs-input", "pid": 1, "pid_start": 1})
            d.prune()
        self.assertEqual(calls, [(None, "working"), ("working", "needs-input"), ("needs-input", None)])

    def test_status_since_only_moves_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Registry(Path(tmp) / "r.json")
            since = r.update("s1", "claude", "working")["status_since"]
            self.assertEqual(r.update("s1", "claude", "working")["status_since"], since)
            self.assertGreaterEqual(r.update("s1", "claude", "idle")["status_since"], since)


if __name__ == "__main__":
    unittest.main()
