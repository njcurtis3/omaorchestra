import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra.mcp import health, inventory, probe, serve


def rpc(method, request_id=1, **params):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


class ServeTest(unittest.TestCase):
    def test_handshake_and_tools(self):
        init = serve.handle(rpc("initialize", protocolVersion="2025-03-26"))
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(serve.handle(rpc("initialize", protocolVersion="1999-01-01"))["result"]["protocolVersion"],
                         serve.SUPPORTED[0], "an unknown version gets ours")
        self.assertIsNone(serve.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [t["name"] for t in serve.handle(rpc("tools/list"))["result"]["tools"]]
        self.assertEqual(names, ["list_sessions", "get_session", "list_queue", "queue_task"])
        self.assertEqual(serve.handle(rpc("nope"))["error"]["code"], -32601)
        self.assertEqual(serve.handle(rpc("tools/call", name="nope", arguments={}))["error"]["code"], -32602)

    def test_tool_errors_are_results_not_crashes(self):
        from omaorchestra import client
        with mock.patch.object(client, "request", side_effect=client.DaemonUnavailable("down")):
            reply = serve.handle(rpc("tools/call", name="list_sessions", arguments={}))
        self.assertTrue(reply["result"]["isError"])

    def test_queue_task_is_always_paused(self):
        sent = []

        def fake(payload, timeout=1.0):
            sent.append(payload)
            if payload["cmd"] == "list":
                return {"sessions": [{"id": "abc123", "cwd": "/w", "transcript_path": None}]}
            return {"ok": True, "item": {"id": "q1"}}
        with mock.patch("omaorchestra.client.request", fake), mock.patch("subprocess.run"):
            reply = serve.handle(rpc("tools/call", name="queue_task",
                                     arguments={"task": "write docs", "folder": "/w", "handoff_from": "abc"}))
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(payload["state"], "paused")
        queued = [p for p in sent if p["cmd"] == "queue-add"][0]
        self.assertTrue(queued["paused"])
        self.assertIn("Handed off from session abc123", queued["item"]["task"])


class StdioProbeTest(unittest.TestCase):
    def test_real_server(self):
        r = probe.stdio(str(ROOT / "bin" / "omaorchestra"), ["mcp", "serve"])
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual((r["server"], len(r["tools"])), ("omaorchestra", 4))

    def test_failures(self):
        self.assertIn("not installed", probe.stdio("no-such-mcp-server")["error"])
        self.assertIn("exited", probe.stdio("true")["error"])
        slow = probe.stdio("sleep", ["30"], timeout=1)
        self.assertFalse(slow["ok"])
        self.assertIn("no answer", slow["error"])
        self.assertLess(slow["seconds"], 5)


class FakeHttpMcp(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer ok":
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake", "version": "9"}, "capabilities": {}}
        else:
            assert self.headers.get("Mcp-Session-Id") == "s-1", "session id not sent back"
            cursor = (message.get("params") or {}).get("cursor")
            result = {"tools": [{"name": "b"}]} if cursor else {"tools": [{"name": "a"}], "nextCursor": "p2"}
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
        self.send_response(200)
        self.send_header("Mcp-Session-Id", "s-1")
        if message["method"] == "tools/list":  # answer this one as an event stream
            self.send_header("Content-Type", "text/event-stream")
            body = f"event: message\ndata: {body}\n\n"
        else:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())


class HttpProbeTest(unittest.TestCase):
    def test_streamable_http(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHttpMcp)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
        try:
            ok = probe.http(url, {"Authorization": "Bearer ok"})
            denied = probe.http(url, {})
        finally:
            server.shutdown()
        self.assertTrue(ok["ok"], ok["error"])
        self.assertEqual(([t["name"] for t in ok["tools"]], ok["server"]), (["a", "b"], "fake"))
        self.assertIn("needs authentication", denied["error"])
        self.assertIn("cannot reach", probe.http("http://127.0.0.1:9/mcp", timeout=2)["error"])


class HealthTest(unittest.TestCase):
    def test_checks_with_expanded_variables_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude.json").write_text(json.dumps({"mcpServers": {"self": {
                "command": "${OMA_TEST_BIN}", "args": ["mcp", "serve"]}}}))
            with mock.patch.dict(os.environ, {"OMAORCHESTRA_AGENT_HOME": tmp, "OMAORCHESTRA_STATE_DIR": tmp,
                                              "OMA_TEST_BIN": str(ROOT / "bin" / "omaorchestra")}):
                (entry,) = inventory.claude()
                saved = health.check_all([entry])
                self.assertTrue(saved[inventory.key(entry)]["ok"], saved)
                self.assertIn(inventory.key(entry), health.results())
        self.assertEqual(health._expand("${NOPE_X:-fallback}/x"), "fallback/x")


if __name__ == "__main__":
    unittest.main()
