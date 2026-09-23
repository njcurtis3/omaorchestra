import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra import daemon
from omaorchestra.registry import Registry


class SubscribeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sock = Path(self.tmp.name) / "o.sock"
        self.registry = Registry(Path(self.tmp.name) / "sessions.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_async(self, scenario, **daemon_args):
        async def main():
            d = daemon.Daemon(self.registry, is_alive=lambda p, s: False, **daemon_args)
            server = await daemon.serve(self.sock, self.registry, d)
            try:
                return await asyncio.wait_for(scenario(d), timeout=5)
            finally:
                server.close()
                await server.wait_closed()
        return asyncio.run(main())

    async def connect(self):
        return await asyncio.open_unix_connection(str(self.sock))

    async def send(self, payload):
        reader, writer = await self.connect()
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        return response

    async def subscribe(self):
        reader, writer = await self.connect()
        writer.write(b'{"cmd": "subscribe"}\n')
        await writer.drain()
        snapshot = json.loads(await reader.readline())
        return reader, writer, snapshot

    @staticmethod
    async def next_event(reader):
        """The next session event, skipping queue updates (sent when the
        number of busy agents changes)."""
        while True:
            message = json.loads(await reader.readline())
            if message.get("event") != "queue":
                return message

    def update(self, sid, status, **extra):
        return {"cmd": "update", "session_id": sid, "agent": "claude", "status": status, **extra}

    def test_snapshot_then_changes_and_removals(self):
        self.registry.update("old", "claude", "idle")

        async def scenario(d):
            reader, writer, snapshot = await self.subscribe()
            await self.send(self.update("s1", "working"))
            first = await self.next_event(reader)
            await self.send(self.update("s1", "working"))  # same status still streams (updated time)
            second = await self.next_event(reader)
            await self.send({"cmd": "remove", "session_id": "s1", "reason": "dismissed"})
            removed = await self.next_event(reader)
            await self.send({"cmd": "remove", "session_id": "old"})
            ended = await self.next_event(reader)
            writer.close()
            return snapshot, first, second, removed, ended

        snapshot, first, second, removed, ended = self.run_async(scenario)
        self.assertEqual([s["id"] for s in snapshot["sessions"]], ["old"])
        self.assertEqual((first["event"], first["session"]["id"], first["session"]["status"]), ("session", "s1", "working"))
        self.assertEqual(second["event"], "session")
        self.assertEqual(removed, {"event": "removed", "id": "s1", "reason": "dismissed"})
        self.assertEqual(ended, {"event": "removed", "id": "old", "reason": "session-end"})

    def test_prune_streams_process_gone(self):
        async def scenario(d):
            reader, writer, _ = await self.subscribe()
            await self.send(self.update("s1", "working", pid=5, pid_start=50))
            await self.next_event(reader)
            d.prune()
            gone = await self.next_event(reader)
            writer.close()
            return gone

        self.assertEqual(self.run_async(scenario), {"event": "removed", "id": "s1", "reason": "process-gone"})

    def test_many_subscribers_and_cleanup_on_disconnect(self):
        async def scenario(d):
            subs = [await self.subscribe() for _ in range(3)]
            self.assertEqual(len(d.subscribers), 3)
            await self.send(self.update("s1", "idle"))
            got = [await self.next_event(r) for r, _, _ in subs]
            for _, w, _ in subs:
                w.close()
            for _ in range(50):
                if not d.subscribers:
                    break
                await asyncio.sleep(0.02)
            return got, len(d.subscribers)

        got, remaining = self.run_async(scenario)
        self.assertEqual([g["session"]["id"] for g in got], ["s1"] * 3)
        self.assertEqual(remaining, 0)

    def test_slow_subscriber_is_dropped_not_buffered_forever(self):
        async def scenario(d):
            reader, writer, _ = await self.subscribe()
            # The stream task drains the queue as fast as it can write, so
            # fill it directly while it is suspended.
            (queue,) = d.subscribers
            for i in range(3):
                d.publish({"event": "session", "session": {"id": f"s{i}"}})
            dropped = queue not in d.subscribers
            lines = []
            while line := await reader.readline():
                lines.append(json.loads(line))
            writer.close()
            return dropped, lines

        dropped, lines = self.run_async(scenario, backlog=2)
        self.assertTrue(dropped)
        # It gets what was queued before the overflow, then the connection closes.
        self.assertEqual([m["session"]["id"] for m in lines], ["s0", "s1"])

    def test_plain_requests_still_work_alongside(self):
        async def scenario(d):
            reader, writer, _ = await self.subscribe()
            ping = await self.send({"cmd": "ping"})
            writer.close()
            return ping

        self.assertTrue(self.run_async(scenario)["ok"])


class ShutdownWithSubscriberTest(unittest.TestCase):
    def test_sigterm_does_not_wait_for_subscribers(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "OMAORCHESTRA_SOCKET": str(Path(tmp) / "o.sock"),
                   "OMAORCHESTRA_STATE_DIR": str(Path(tmp) / "state"),
                   "OMAORCHESTRA_CONFIG": str(Path(tmp) / "none.toml")}
            bin_ = str(ROOT / "bin" / "omaorchestra")
            d = subprocess.Popen([bin_, "daemon"], env=env, stderr=subprocess.DEVNULL)
            watcher = None
            try:
                for _ in range(50):
                    if Path(env["OMAORCHESTRA_SOCKET"]).exists():
                        break
                    time.sleep(0.1)
                watcher = subprocess.Popen([bin_, "watch", "--json"], env=env, stdout=subprocess.PIPE, text=True)
                watcher.stdout.readline()  # subscribed
                started = time.monotonic()
                d.terminate()
                code = d.wait(timeout=5)
                took = time.monotonic() - started
                watcher.wait(timeout=5)  # its connection was closed, so it ends too
            finally:
                for p in (d, watcher):
                    if p and p.poll() is None:
                        p.kill()
        self.assertEqual(code, 0)
        self.assertLess(took, 2)


class WatchCliTest(unittest.TestCase):
    def test_watch_prints_live_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "OMAORCHESTRA_SOCKET": str(Path(tmp) / "o.sock"),
                   "OMAORCHESTRA_STATE_DIR": str(Path(tmp) / "state"),
                   "OMAORCHESTRA_CONFIG": str(Path(tmp) / "none.toml")}
            bin_ = str(ROOT / "bin" / "omaorchestra")
            d = subprocess.Popen([bin_, "daemon"], env=env, stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    if Path(env["OMAORCHESTRA_SOCKET"]).exists():
                        break
                    time.sleep(0.1)
                w = subprocess.Popen([bin_, "watch", "--json"], env=env, stdout=subprocess.PIPE, text=True)
                snapshot = json.loads(w.stdout.readline())
                hook = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "watch-1", "cwd": "/tmp"})
                subprocess.run([bin_, "hook", "claude"], input=hook, env=env, text=True)
                change = json.loads(w.stdout.readline())
                w.terminate()
                w.wait(timeout=5)
            finally:
                d.terminate()
                d.wait(timeout=5)
        self.assertEqual(snapshot, {"sessions": []})
        self.assertEqual((change["event"], change["session"]["id"], change["session"]["status"]),
                         ("session", "watch-1", "working"))


if __name__ == "__main__":
    unittest.main()
