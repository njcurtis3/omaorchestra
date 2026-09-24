import contextlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import catalog, keys, providers
from omaorchestra.__main__ import main

GOOD = "good-key"


class FakeProviders(http.server.BaseHTTPRequestHandler):
    """Speaks just enough of each provider's API."""

    def log_message(self, *a):
        pass

    def reply(self, code, body):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        bearer = self.headers.get("Authorization") == f"Bearer {GOOD}"
        if self.path.startswith("/anthropic/v1/models"):
            if self.headers.get("x-api-key") != GOOD or self.headers.get("anthropic-version") != "2023-06-01":
                return self.reply(401, {"error": "bad key"})
            if "after_id=claude-opus-5" in self.path:
                return self.reply(200, {"data": [{"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5",
                                                  "max_input_tokens": 200000, "max_tokens": 64000}], "has_more": False})
            return self.reply(200, {"data": [{"id": "claude-opus-5", "display_name": "Claude Opus 5",
                                              "max_input_tokens": 1000000, "max_tokens": 128000}],
                                    "has_more": True, "last_id": "claude-opus-5"})
        if self.path == "/openrouter/key":
            return self.reply(200, {"data": {"label": "k"}}) if bearer else self.reply(401, {})
        if self.path == "/openrouter/models":
            return self.reply(200, {"data": [{"id": "anthropic/claude-sonnet-5", "name": "Claude Sonnet 5",
                                              "context_length": 1000000, "pricing": {"prompt": "0.000002", "completion": "0.00001"},
                                              "top_provider": {"max_completion_tokens": 128000}}]})
        if self.path == "/openai/models":
            return self.reply(200, {"data": [{"id": "gpt-5"}]}) if bearer else self.reply(401, {})
        if self.path == "/ollama/api/tags":
            return self.reply(200, {"models": [{"name": "llama3:8b"}]})
        if self.path == "/broken/models":
            return self.reply(200, "not json")
        self.reply(404, {})


class ProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeProviders)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_PROVIDERS": os.path.join(self.tmp.name, "p.json"),
                                                "OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state")})
        self.env.start()
        self.stored = {}
        self.lookup = self.stored.get

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def add(self, kind, suffix=None):
        return providers.add(kind, base_url=f"{self.base}/{suffix or kind}")

    def test_registry(self):
        self.add("anthropic")
        with mock.patch.object(keys, "clear"):
            with self.assertRaisesRegex(providers.ProviderError, "already"):
                self.add("anthropic")
            providers.add("openrouter", provider_id="or-work")
            self.assertEqual([p["id"] for p in providers.load()], ["anthropic", "or-work"])
            self.assertEqual(providers.get("or-work")["base_url"], "https://openrouter.ai/api/v1")
            for bad in (("nope", None, None), ("openai", "Bad Id", None), ("openai", "x", "ftp://x")):
                with self.assertRaises(providers.ProviderError):
                    providers.add(*bad)
            providers.remove("anthropic")
        self.assertEqual([p["id"] for p in providers.load()], ["or-work"])
        self.assertNotIn(GOOD, Path(os.environ["OMAORCHESTRA_PROVIDERS"]).read_text(), "no secrets in the file")

    def test_anthropic_paginates_and_prices(self):
        p = self.add("anthropic")
        self.stored["anthropic"] = GOOD
        models = catalog.fetch(p, lookup=self.lookup)
        self.assertEqual([m["id"] for m in models], ["claude-haiku-4-5-20251001", "claude-opus-5"])
        haiku = models[0]
        self.assertEqual((haiku["context"], haiku["max_output"], haiku["input_price"], haiku["output_price"]),
                         (200000, 64000, 1.0, 5.0))
        self.assertIn("2 model(s)", providers.test(p, lookup=self.lookup))

    def test_bad_or_missing_keys(self):
        p = self.add("anthropic")
        with self.assertRaisesRegex(providers.ProviderError, "no API key stored"):
            providers.test(p, lookup=self.lookup)
        self.stored["anthropic"] = "wrong"
        with self.assertRaisesRegex(providers.ProviderError, "rejected"):
            providers.test(p, lookup=self.lookup)

    def test_openrouter_checks_the_key_and_converts_prices(self):
        p = self.add("openrouter")
        self.stored["openrouter"] = "wrong"
        with self.assertRaisesRegex(providers.ProviderError, "rejected"):
            catalog.fetch(p, lookup=self.lookup)
        self.stored["openrouter"] = GOOD
        (m,) = catalog.fetch(p, lookup=self.lookup)
        self.assertEqual((m["input_price"], m["output_price"], m["context"], m["max_output"]), (2.0, 10.0, 1000000, 128000))

    def test_openai_and_ollama(self):
        self.stored["openai"] = GOOD
        self.assertEqual([m["id"] for m in catalog.fetch(self.add("openai"), lookup=self.lookup)], ["gpt-5"])
        (m,) = catalog.fetch(self.add("ollama"), lookup=self.lookup)
        self.assertEqual((m["id"], m["input_price"]), ("llama3:8b", 0.0))

    def test_unreachable_and_non_json(self):
        dead = providers.add("ollama", provider_id="dead", base_url="http://127.0.0.1:9")
        with self.assertRaisesRegex(providers.ProviderError, "cannot reach"):
            catalog.fetch(dead, lookup=self.lookup)
        broken = providers.add("openai", provider_id="broken", base_url=f"{self.base}/broken")
        self.stored["broken"] = GOOD
        with self.assertRaisesRegex(providers.ProviderError, "did not return JSON"):
            catalog.fetch(broken, lookup=self.lookup)

    def test_refresh_caches_and_keeps_models_when_a_provider_fails(self):
        self.stored["anthropic"] = GOOD
        self.add("anthropic")
        cache = catalog.refresh(lookup=self.lookup)
        self.assertEqual(len(cache["anthropic"]["models"]), 2)
        self.stored["anthropic"] = "wrong"
        cache = catalog.refresh(lookup=self.lookup)
        self.assertIn("rejected", cache["anthropic"]["error"])
        self.assertEqual(len(cache["anthropic"]["models"]), 2, "the last good list is kept")
        ids = [m["id"] for m in catalog.all_models()]
        self.assertEqual(ids[:4], ["fable", "opus", "sonnet", "haiku"])
        self.assertIn("claude-opus-5", ids)

    def test_prices_for_dated_snapshots(self):
        self.assertEqual(catalog.anthropic_price("claude-haiku-4-5-20251001"), (1.0, 5.0))
        self.assertEqual(catalog.anthropic_price("claude-opus-5-5"), (4.0, 20.0), "not confused with claude-opus-5")
        self.assertEqual(catalog.anthropic_price("gpt-5"), (None, None))


class KeyringTest(unittest.TestCase):
    """The real keyring, under a service name used only by tests."""

    def setUp(self):
        self.service = mock.patch.object(keys, "SERVICE", "omaorchestra-test")
        self.service.start()
        if keys.lookup("probe") is None and not self.keyring_works():
            self.skipTest("no Secret Service keyring")

    def tearDown(self):
        keys.clear("t1")
        self.service.stop()

    @staticmethod
    def keyring_works():
        try:
            keys.store("probe", "x")
            keys.clear("probe")
            return True
        except keys.KeyError_:
            return False

    def test_store_lookup_clear(self):
        keys.store("t1", "  sk-test-123\n")
        self.assertEqual(keys.lookup("t1"), "sk-test-123")
        keys.clear("t1")
        self.assertIsNone(keys.lookup("t1"))
        with self.assertRaises(keys.KeyError_):
            keys.store("t1", "   ")

    def test_cli_key_list_remove(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"OMAORCHESTRA_PROVIDERS": os.path.join(tmp, "p.json")}):
            providers.add("openai", provider_id="t1")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO("sk-from-stdin\n")):
                self.assertEqual(main(["provider", "key", "t1", "--stdin"]), 0)
                self.assertEqual(main(["provider", "list"]), 0)
                self.assertEqual(main(["provider", "remove", "t1"]), 0)
            self.assertIn("key stored", out.getvalue())
            self.assertNotIn("sk-from-stdin", out.getvalue())
            self.assertIsNone(keys.lookup("t1"), "removing a provider removes its key")


if __name__ == "__main__":
    unittest.main()
