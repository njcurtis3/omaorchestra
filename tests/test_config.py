import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra import config, daemon
from omaorchestra.__main__ import main


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, text):
        self.path.write_text(text)
        return config.load(self.path)

    def assertRejects(self, text, fragment):
        with self.assertRaises(config.ConfigError) as caught:
            self.load(text)
        self.assertIn(fragment, str(caught.exception))
        self.assertIn(str(self.path), str(caught.exception))

    def test_missing_file_is_all_defaults(self):
        self.assertEqual(config.load(self.path), config.defaults())

    def test_example_config_is_valid_and_matches_defaults(self):
        self.assertEqual(config.load(ROOT / "config.example.toml"), config.defaults())

    def test_partial_file_merges_over_defaults(self):
        c = self.load("[daemon]\nprune_interval = 10\n")
        self.assertEqual(c["daemon"]["prune_interval"], 10)
        self.assertEqual(c["agents"]["enabled"], ["claude"])

    def test_empty_agent_list_is_allowed(self):
        self.assertEqual(self.load("[agents]\nenabled = []\n")["agents"]["enabled"], [])

    def test_rejections(self):
        cases = [
            ("[daemon]\nprune_intervall = 10\n", "unknown key daemon.prune_intervall"),
            ("[deamon]\n", "unknown section [deamon]"),
            ("[daemon]\nprune_interval = 1\n", "daemon.prune_interval must be a whole number from 5"),
            ("[daemon]\nprune_interval = true\n", "daemon.prune_interval must be a whole number"),
            ('[daemon]\nprune_interval = "30"\n', "daemon.prune_interval must be a whole number"),
            ('[agents]\nenabled = ["claude", "gpt"]\n', "unknown agent gpt"),
            ('[agents]\nenabled = "claude"\n', "must be a list"),
            ("[tasks]\nisolate_with_worktrees = 1\n", "must be true or false"),
            ("daemon = 5\n", "[daemon] must be a table"),
            ("[daemon\n", "config.toml"),
        ]
        for text, fragment in cases:
            with self.subTest(text=text):
                self.assertRejects(text, fragment)

    def test_load_or_defaults_never_raises(self):
        self.path.write_text("[broken\n")
        self.assertEqual(config.load_or_defaults(self.path), config.defaults())

    def test_env_overrides_path(self):
        with mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": str(self.path)}):
            self.assertEqual(config.path(), self.path)

    def test_to_toml_round_trips(self):
        import tomllib
        c = self.load("[daemon]\nprune_interval = 45\n[tasks]\nisolate_with_worktrees = false\n")
        self.assertEqual(config.validate(tomllib.loads(config.to_toml(c))), c)


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"
        self.env = mock.patch.dict(os.environ, {
            "OMAORCHESTRA_CONFIG": str(self.path),
            "OMAORCHESTRA_SOCKET": str(Path(self.tmp.name) / "o.sock"),
            "OMAORCHESTRA_STATE_DIR": str(Path(self.tmp.name) / "state"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, stdin=""):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch("sys.stdin", io.StringIO(stdin)):
            code = main(list(argv))
        return code, out.getvalue()

    def test_daemon_exits_2_on_bad_config(self):
        self.path.write_text("[daemon]\nprune_interval = 0\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(daemon.run(), daemon.CONFIG_ERROR_EXIT)
        self.assertIn("prune_interval", err.getvalue())
        self.assertFalse((Path(self.tmp.name) / "o.sock").exists())

    def test_hook_drops_events_for_disabled_agents(self):
        self.path.write_text("[agents]\nenabled = []\n")
        with mock.patch("omaorchestra.client.request") as request:
            code, _ = self.run_cli("hook", "claude", stdin=json.dumps({"hook_event_name": "Stop", "session_id": "s"}))
        self.assertEqual(code, 0)
        request.assert_not_called()

    def test_hook_uses_defaults_when_config_is_broken(self):
        self.path.write_text("[broken\n")
        with mock.patch("omaorchestra.client.request") as request:
            code, _ = self.run_cli("hook", "claude", stdin=json.dumps({"hook_event_name": "Stop", "session_id": "s"}))
        self.assertEqual(code, 0)
        request.assert_called_once()

    def test_config_commands(self):
        self.assertEqual(self.run_cli("config", "path")[1].strip(), str(self.path))
        code, out = self.run_cli("config", "check")
        self.assertEqual(code, 0)
        self.assertIn("using defaults", out)
        self.path.write_text("[daemon]\nprune_interval = 60\n")
        self.assertIn("prune_interval = 60", self.run_cli("config", "show")[1])
        self.path.write_text("[daemon]\nnope = 1\n")
        code, out = self.run_cli("config", "check")
        self.assertEqual(code, 1)
        self.assertIn("unknown key daemon.nope", out)


if __name__ == "__main__":
    unittest.main()
