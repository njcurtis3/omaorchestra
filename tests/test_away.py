import asyncio
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __main__ as cli, away, config, daemon, remote
from omaorchestra.registry import Registry


class AwayStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "away.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_auto_is_away_when_locked_or_idle(self):
        a = away.Away(self.path)
        self.assertEqual(a.mode, "auto")
        self.assertFalse(a.away)
        a.update(idle=True)
        self.assertEqual(a.reason, "idle")
        a.update(locked=True)
        self.assertEqual(a.reason, "locked")
        a.update(locked=None, idle=None)
        self.assertFalse(a.away, "signals that cannot be read count as at the desk")

    def test_on_and_off_ignore_the_desk(self):
        a = away.Away(self.path)
        a.update(mode="off", locked=True, idle=True)
        self.assertFalse(a.away)
        a.update(mode="on", locked=False, idle=False)
        self.assertEqual(a.reason, "on")

    def test_mode_survives_a_restart_and_the_file_says_where_you_are(self):
        a = away.Away(self.path)
        a.update(mode="on", push=True)
        saved = json.loads(self.path.read_text())
        self.assertEqual((saved["mode"], saved["away"], saved["reason"], saved["push"]), ("on", True, "on", True))
        self.assertEqual(away.Away(self.path).mode, "on")

    def test_a_bad_file_falls_back_to_auto(self):
        self.path.write_text("[not a dict]")
        self.assertEqual(away.Away(self.path).mode, "auto")

    def test_changes_are_reported_once(self):
        seen = []
        a = away.Away(self.path, on_change=seen.append)
        self.assertFalse(a.update(locked=False))  # still at the desk
        self.assertTrue(a.update(idle=True))
        self.assertFalse(a.update(idle=True))
        a.update(idle=False)
        self.assertEqual([(s["away"], s["reason"]) for s in seen], [(True, "idle"), (False, None)])


class LockTest(unittest.TestCase):
    def fake(self, stdout="", code=0, error=None):
        def run(cmd, **kw):
            self.assertEqual(cmd, ["omarchy-shell", "lock", "isLocked"])
            if error:
                raise error
            return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")
        return run

    def test_answers(self):
        self.assertIs(away.is_locked(self.fake("true\n")), True)
        self.assertIs(away.is_locked(self.fake("false\n")), False)
        self.assertIsNone(away.is_locked(self.fake("", code=1)))
        self.assertIsNone(away.is_locked(self.fake(error=FileNotFoundError())))
        self.assertIsNone(away.is_locked(self.fake(error=subprocess.TimeoutExpired("x", 2))))


def message(obj, opcode, payload=b""):
    return struct.pack("<II", obj, (8 + len(payload)) << 16 | opcode) + payload


def wl_string(text):
    data = text.encode() + b"\0"
    return struct.pack("<I", len(data)) + data + b"\0" * (-len(data) % 4)


class FakeCompositor:
    """Just enough of a Wayland compositor: a registry, and idle
    notifications that idle and resume on cue."""

    def __init__(self, path, globals_=(("wl_seat", 7), ("ext_idle_notifier_v1", 2))):
        self.path = path
        self.globals = globals_
        self.requests = []  # (object, opcode, payload)
        self.notifications = []  # (id, timeout ms)
        self.writer = None
        self.connected = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_unix_server(self.serve, path=str(self.path))

    async def serve(self, reader, writer):
        self.writer = writer
        registry = None
        try:
            while True:
                obj, word = struct.unpack("<II", await reader.readexactly(8))
                payload = await reader.readexactly((word >> 16) - 8)
                opcode = word & 0xFFFF
                self.requests.append((obj, opcode, payload))
                if (obj, opcode) == (1, 1):
                    registry, = struct.unpack("<I", payload)
                elif (obj, opcode) == (1, 0):
                    for name, (interface, version) in enumerate(self.globals, 1):
                        writer.write(message(registry, 0, struct.pack("<I", name) + wl_string(interface)
                                             + struct.pack("<I", version)))
                    writer.write(message(struct.unpack("<I", payload)[0], 0, struct.pack("<I", 0)))
                elif opcode == 1 and len(payload) == 12:  # get_idle_notification(id, timeout, seat)
                    new, timeout, _seat = struct.unpack("<III", payload)
                    self.notifications.append((new, timeout))
                    self.connected.set()
        except asyncio.IncompleteReadError:
            pass

    def emit(self, opcode):
        self.writer.write(message(self.notifications[-1][0], opcode))

    def bound(self):
        """The interfaces the client bound, from wl_registry.bind requests."""
        names = []
        for obj, opcode, payload in self.requests:
            if obj not in (1,) and opcode == 0 and len(payload) > 12:
                names.append(away._read_string(payload, 4)[0])
        return names


class IdleWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wayland-9"

    def tearDown(self):
        self.tmp.cleanup()

    async def settle(self, seen, count):
        for _ in range(200):
            if len(seen) >= count:
                return
            await asyncio.sleep(0.01)

    def test_idle_and_back(self):
        async def main():
            compositor = FakeCompositor(self.path)
            await compositor.start()
            seen = []
            watch = away.IdleWatch(10, seen.append, path=str(self.path))
            watch.start()
            await asyncio.wait_for(compositor.connected.wait(), 2)
            await self.settle(seen, 1)
            compositor.emit(0)  # idled
            await self.settle(seen, 2)
            compositor.emit(1)  # resumed
            await self.settle(seen, 3)
            watch.notify_after(3)  # a changed setting
            await self.settle(seen, 4)
            await asyncio.sleep(0.05)
            watch.stop()
            compositor.server.close()
            return compositor, seen

        compositor, seen = asyncio.run(main())
        self.assertEqual(seen, [False, True, False, False])
        self.assertEqual(compositor.bound(), ["wl_seat", "ext_idle_notifier_v1"])
        self.assertEqual([t for _, t in compositor.notifications], [600_000, 180_000])

    def test_no_idle_protocol(self):
        async def main():
            compositor = FakeCompositor(self.path, globals_=(("wl_seat", 7),))
            await compositor.start()
            seen = []
            watch = away.IdleWatch(10, seen.append, path=str(self.path))
            with self.assertLogs("omaorchestra", "WARNING") as logs:
                watch.start()
                await self.settle(seen, 1)
            watch.stop()
            compositor.server.close()
            return seen, logs

        seen, logs = asyncio.run(main())
        self.assertEqual(seen, [None])
        self.assertIn("does not offer idle notifications", logs.output[0])

    def test_no_display(self):
        self.assertIsNone(away.display_path({}))
        self.assertEqual(away.display_path({"WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/1"}),
                         "/run/user/1/wayland-1")
        self.assertEqual(away.display_path({"WAYLAND_DISPLAY": "/tmp/w"}), "/tmp/w")


class FakeWatch:
    made = []

    def __init__(self, minutes, on_idle):
        self.minutes, self.on_idle = minutes, on_idle
        self.stopped = False
        FakeWatch.made.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def notify_after(self, minutes):
        self.minutes = minutes


class DaemonAwayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "c.toml"
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_CONFIG": str(self.config)})
        self.env.start()
        FakeWatch.made = []
        patch = mock.patch.object(away, "IdleWatch", FakeWatch)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def make(self, push=True):
        settings = config.defaults()
        settings["remote"]["push"] = push
        d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        self.locked = False
        d.is_locked = lambda: self.locked
        return d

    def test_mode_over_the_socket(self):
        d = self.make()
        self.assertEqual(d.handle({"cmd": "away"})["away"]["mode"], "auto")
        state = d.handle({"cmd": "away", "mode": "on"})["away"]
        self.assertEqual((state["mode"], state["away"], state["reason"]), ("on", True, "on"))
        self.assertFalse(d.handle({"cmd": "away", "mode": "maybe"})["ok"])
        self.assertEqual(json.loads((Path(self.tmp.name) / "away.json").read_text())["mode"], "on")

    def test_subscribers_hear_of_it(self):
        d = self.make()
        queue = asyncio.Queue()
        d.subscribers.add(queue)
        d.handle({"cmd": "away", "mode": "off"})
        self.assertEqual(queue.get_nowait()["event"], "away")

    def test_watches_only_with_push_on_and_mode_auto(self):
        async def main():
            d = self.make()
            d.sync_away()
            self.assertEqual(len(FakeWatch.made), 1)
            await asyncio.sleep(0)
            FakeWatch.made[0].on_idle(True)
            self.assertEqual(d.away.reason, "idle")
            d.handle({"cmd": "away", "mode": "off"})
            self.assertTrue(FakeWatch.made[0].stopped)
            self.assertIsNone(d.away.idle)
            d.handle({"cmd": "away", "mode": "auto"})
            self.assertEqual(len(FakeWatch.made), 2)
            self.config.write_text("[remote]\npush = false\n")
            d.reload()
            self.assertTrue(FakeWatch.made[1].stopped)
            self.assertIsNone(d.idle_watch)
        asyncio.run(main())

    def test_a_changed_idle_time_reaches_the_watch(self):
        async def main():
            d = self.make()
            d.sync_away()
            self.config.write_text("[remote]\npush = true\naway_after = 25\n")
            d.reload()
            self.assertEqual(FakeWatch.made[0].minutes, 25)
            self.assertEqual(d.away.state()["after"], 25)
            d.sync_away()
        asyncio.run(main())

    def test_the_lock_screen_is_checked_right_before_a_push(self):
        async def main():
            d = self.make()
            d.sync_away()
            self.assertFalse(await d.is_away())
            self.locked = True
            self.assertTrue(await d.is_away())
            self.assertEqual(d.away.reason, "locked")
            d.sync_away()
            d.handle({"cmd": "away", "mode": "off"})
        asyncio.run(main())

    def test_snapshot_carries_it(self):
        async def main():
            d = self.make()
            reader = asyncio.StreamReader()
            reader.feed_eof()
            sent = []

            class Writer:
                def write(self, data):
                    sent.append(json.loads(data))

                async def drain(self):
                    pass

            await d.stream(reader, Writer())
            return sent
        self.assertEqual(asyncio.run(main())[0]["away"]["mode"], "auto")


class PusherAwayTest(unittest.TestCase):
    def test_nothing_goes_out_at_the_desk(self):
        async def main(here):
            sent = []

            async def is_away():
                return not here
            p = remote.Pusher({**config.defaults()["remote"], "push": True}, {"finished_after": 120},
                              send=lambda *a: sent.append(a[1]), away=is_away)
            p.changed(None, {"id": "s1", "status": "needs-input", "cwd": "/w/proj"})
            await asyncio.gather(*p.tasks)
            return sent
        self.assertEqual(asyncio.run(main(here=True)), [])
        self.assertEqual(asyncio.run(main(here=False)), ["needs-you"])


class CliAwayTest(unittest.TestCase):
    def run_cli(self, *argv, answer=None):
        requests = []

        def request(payload, timeout=1.0):
            requests.append(payload)
            return answer
        out = io.StringIO()
        with mock.patch.object(cli.client, "request", request), redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue(), requests

    def state(self, **changes):
        return {"mode": "auto", "away": False, "reason": None, "since": 0, "push": True, "after": 10,
                "locked": False, "idle": False, **changes}

    def test_show(self):
        code, out, requests = self.run_cli("away", answer={"ok": True, "away": self.state()})
        self.assertEqual(requests, [{"cmd": "away"}])
        self.assertIn("after 10 minutes without input", out)
        self.assertIn("now   at the desk", out)

    def test_set(self):
        code, out, requests = self.run_cli("away", "on", answer={"ok": True, "away": self.state(
            mode="on", away=True, reason="on")})
        self.assertEqual(requests, [{"cmd": "away", "mode": "on"}])
        self.assertIn("now   away since", out)

    def test_says_when_push_is_off_or_a_signal_is_missing(self):
        _, out, _ = self.run_cli("away", answer={"ok": True, "away": self.state(idle=None)})
        self.assertIn("cannot read idle time", out)
        _, out, _ = self.run_cli("away", answer={"ok": True, "away": self.state(push=False)})
        self.assertIn("push is off", out)

    def test_daemon_down(self):
        def request(payload, timeout=1.0):
            raise cli.client.DaemonUnavailable("no")
        with mock.patch.object(cli.client, "request", request), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.main(["away"]), 1)


if __name__ == "__main__":
    unittest.main()
