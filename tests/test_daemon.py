import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, hooks
from omaorchestra.registry import Registry


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sessions.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_persists_and_reloads(self):
        Registry(self.path).update("s1", "claude", "working", cwd="/w")
        reloaded = Registry(self.path).list()
        self.assertEqual([(s["id"], s["status"], s["cwd"]) for s in reloaded], [("s1", "working", "/w")])

    def test_update_keeps_start_time_and_cwd(self):
        r = Registry(self.path)
        first = dict(r.update("s1", "claude", "idle", cwd="/w"))
        second = r.update("s1", "claude", "working")
        self.assertEqual(second["started"], first["started"])
        self.assertEqual(second["cwd"], "/w")

    def test_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            Registry(self.path).update("s1", "claude", "sleeping")

    def test_remove(self):
        r = Registry(self.path)
        r.update("s1", "claude", "idle")
        self.assertTrue(r.remove("s1"))
        self.assertIsNone(r.remove("s1"))
        self.assertEqual(Registry(self.path).list(), [])

    def test_corrupt_file_starts_empty(self):
        self.path.write_text("{not json")
        self.assertEqual(Registry(self.path).list(), [])


class HookMappingTest(unittest.TestCase):
    def test_notification_needs_input_with_message(self):
        req = hooks.request_for({"hook_event_name": "Notification", "session_id": "s1", "message": "Allow Bash?"})
        self.assertEqual((req["status"], req["message"]), ("needs-input", "Allow Bash?"))

    def test_session_end_removes(self):
        req = hooks.request_for({"hook_event_name": "SessionEnd", "session_id": "s1"})
        self.assertEqual(req, {"cmd": "remove", "session_id": "s1"})

    def test_ignores_unknown_events_and_missing_ids(self):
        self.assertIsNone(hooks.request_for({"hook_event_name": "PreCompact", "session_id": "s1"}))
        self.assertIsNone(hooks.request_for({"hook_event_name": "Stop"}))

    def test_snippet_covers_every_event(self):
        self.assertEqual(set(hooks.settings_snippet()["hooks"]), set(hooks.CLAUDE_EVENTS))


class SocketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sock = Path(self.tmp.name) / "test.sock"
        self.registry = Registry(Path(self.tmp.name) / "sessions.json")

    def tearDown(self):
        self.tmp.cleanup()

    async def roundtrip(self, *requests):
        server = await daemon.serve(self.sock, self.registry)
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.sock))
            responses = []
            for r in requests:
                writer.write(json.dumps(r).encode() + b"\n")
                await writer.drain()
                responses.append(json.loads(await reader.readline()))
            writer.close()
            return responses
        finally:
            server.close()
            await server.wait_closed()

    def test_update_then_list(self):
        _, listed = asyncio.run(self.roundtrip(
            {"cmd": "update", "session_id": "s1", "agent": "claude", "status": "working"},
            {"cmd": "list"},
        ))
        self.assertEqual([s["id"] for s in listed["sessions"]], ["s1"])

    def test_bad_requests_get_errors_not_disconnects(self):
        bad, unknown, ping = asyncio.run(self.roundtrip(
            {"cmd": "update"}, {"cmd": "nope"}, {"cmd": "ping"},
        ))
        self.assertFalse(bad["ok"])
        self.assertFalse(unknown["ok"])
        self.assertTrue(ping["ok"])

    def test_socket_is_user_only(self):
        async def check():
            server = await daemon.serve(self.sock, self.registry)
            mode = stat.S_IMODE(os.stat(self.sock).st_mode)
            server.close()
            await server.wait_closed()
            return mode
        self.assertEqual(asyncio.run(check()), 0o600)

    def test_refuses_to_start_twice(self):
        async def start_twice():
            server = await daemon.serve(self.sock, self.registry)
            try:
                with self.assertRaises(daemon.AlreadyRunning):
                    await daemon.serve(self.sock, self.registry)
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(start_twice())

    def test_replaces_stale_socket(self):
        self.sock.touch()
        async def start():
            server = await daemon.serve(self.sock, self.registry)
            server.close()
            await server.wait_closed()
        asyncio.run(start())


if __name__ == "__main__":
    unittest.main()
