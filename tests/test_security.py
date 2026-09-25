"""Hardening from the security review (docs/security.md)."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import config, paths, providers, remote


class StateFolderTest(unittest.TestCase):
    def test_created_and_tightened_to_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "state"
            with mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(folder)}):
                self.assertEqual(paths.private_state_dir(), folder)
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
                folder.chmod(0o755)  # an install from before the review
                paths.private_state_dir()
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)


class PlainHttpTest(unittest.TestCase):
    def test_which_servers_count(self):
        self.assertTrue(config.plain_http("http://ntfy.example.org"))
        self.assertTrue(config.plain_http("http://192.168.1.5:8080/"))
        self.assertFalse(config.plain_http("https://ntfy.sh"))
        self.assertFalse(config.plain_http("http://localhost:8080"))
        self.assertFalse(config.plain_http("http://127.0.0.1"))

    def test_the_token_never_goes_over_plain_http(self):
        sent = []
        with self.assertRaises(remote.RemoteError) as caught:
            remote.post("http://ntfy.example.org", {"topic": "t"}, token="secret", opener=sent.append)
        self.assertIn("plain http", str(caught.exception))
        self.assertEqual(sent, [], "nothing was sent")

    def test_without_a_token_plain_http_still_works(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"{}"
        sent = []
        remote.post("http://ntfy.example.org", {"topic": "t"}, opener=lambda r, timeout: sent.append(r) or Response())
        self.assertEqual(len(sent), 1)


class ProviderKeyTest(unittest.TestCase):
    def test_a_keyed_provider_needs_https(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"OMAORCHESTRA_PROVIDERS": os.path.join(tmp, "p.json")}):
            with self.assertRaises(providers.ProviderError):
                providers.add("openrouter", base_url="http://proxy.example.org/api/v1")
            providers.add("openrouter", "local", base_url="http://localhost:4000/api/v1")
        with self.assertRaises(providers.ProviderError) as caught:
            providers.get_json({"id": "x", "kind": "openrouter", "base_url": "http://proxy.example.org"}, "/models",
                               key="sk-secret")
        self.assertIn("plain http", str(caught.exception))


class AppSocketTest(unittest.TestCase):
    def test_in_the_private_runtime_folder_not_tmp(self):
        try:
            from omaorchestra.app import instance
        except ImportError:
            self.skipTest("PySide6 not available")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/4242"}):
            self.assertEqual(instance.server_name(), "/run/user/4242/omaorchestra-app.sock")


class DefaultsTest(unittest.TestCase):
    def test_what_leaves_the_machine_by_default(self):
        """Pushes are off, and when turned on say the least; ntfy over https."""
        remote_defaults = config.defaults()["remote"]
        self.assertFalse(remote_defaults["push"])
        self.assertEqual(remote_defaults["content"], "minimal")
        self.assertTrue(remote_defaults["server"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
