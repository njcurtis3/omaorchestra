import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import claude_settings as cs
from omaorchestra.__main__ import main
from omaorchestra.hooks import CLAUDE_EVENTS

OURS = "/opt/omaorchestra/bin/omaorchestra hook claude"
USER_STOP = {"hooks": [{"type": "command", "command": "notify-send done"}]}


class IsOursTest(unittest.TestCase):
    def test_matches_only_the_form_install_writes(self):
        self.assertTrue(cs.is_ours({"command": OURS}))
        self.assertTrue(cs.is_ours({"command": cs.hook_command("/home/u/my dir/bin/omaorchestra")}))
        for other in ("echo omaorchestra hook claude", "/bin/omaorchestra hook codex",
                      "other-tool hook claude", "notify-send done", "'unbalanced"):
            self.assertFalse(cs.is_ours({"command": other}), other)
        self.assertFalse(cs.is_ours("not a dict"))


class MergeTest(unittest.TestCase):
    def test_install_into_empty(self):
        out = cs.install({}, OURS)
        self.assertEqual(cs.installed_events(out), {e: OURS for e in CLAUDE_EVENTS})

    def test_install_keeps_everything_else(self):
        settings = {"model": "opus", "hooks": {"Stop": [USER_STOP], "PreCompact": [USER_STOP]}}
        out = cs.install(settings, OURS)
        self.assertEqual(out["model"], "opus")
        self.assertEqual(out["hooks"]["Stop"][0], USER_STOP)
        self.assertEqual(out["hooks"]["PreCompact"], [USER_STOP])
        self.assertEqual(settings["hooks"]["Stop"], [USER_STOP], "input must not be mutated")

    def test_install_is_idempotent_and_replaces_old_paths(self):
        once = cs.install({}, OURS)
        self.assertEqual(cs.install(once, OURS), once)
        moved = cs.install(once, "/new/omaorchestra hook claude")
        self.assertEqual(set(cs.installed_events(moved).values()), {"/new/omaorchestra hook claude"})
        self.assertEqual(len(moved["hooks"]["Stop"]), 1)

    def test_remove_restores_the_original(self):
        original = {"model": "opus", "hooks": {"Stop": [USER_STOP]}}
        self.assertEqual(cs.remove(cs.install(original, OURS)), original)
        self.assertEqual(cs.remove(cs.install({"model": "opus"}, OURS)), {"model": "opus"})

    def test_remove_from_a_shared_group_keeps_the_other_hook(self):
        mixed = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": OURS}, USER_STOP["hooks"][0]]}]}}
        self.assertEqual(cs.remove(mixed), {"hooks": {"Stop": [USER_STOP]}})

    def test_rejects_non_object_hooks(self):
        with self.assertRaises(cs.SettingsError):
            cs.install({"hooks": []}, OURS)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_or_empty(self):
        self.assertEqual(cs.load(self.path), {})
        self.path.write_text("  \n")
        self.assertEqual(cs.load(self.path), {})

    def test_load_refuses_bad_json(self):
        for bad in ("{oops", "[1, 2]"):
            self.path.write_text(bad)
            with self.assertRaises(cs.SettingsError):
                cs.load(self.path)

    def test_save_backs_up_and_keeps_mode(self):
        self.path.write_text('{"model": "opus"}')
        os.chmod(self.path, 0o600)
        backup = cs.save(self.path, {"model": "sonnet"})
        self.assertEqual(json.loads(backup.read_text()), {"model": "opus"})
        self.assertEqual(json.loads(self.path.read_text()), {"model": "sonnet"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual([p.name for p in self.path.parent.iterdir() if "tmp" in p.name], [])

    def test_save_new_file_has_no_backup(self):
        self.assertIsNone(cs.save(self.path, {}))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.original = {"model": "opus", "hooks": {"Stop": [USER_STOP]}}
        self.path.write_text(json.dumps(self.original))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_install_status_uninstall_round_trip(self):
        s = str(self.path)
        self.assertEqual(self.run_cli("hooks", "status", "--settings", s)[0], 1)
        code, out = self.run_cli("hooks", "install", "--settings", s, "--command", OURS)
        self.assertEqual(code, 0, out)
        self.assertIn("backup", out)
        self.assertEqual(self.run_cli("hooks", "status", "--settings", s)[0], 0)
        self.assertIn("already up to date", self.run_cli("hooks", "install", "--settings", s, "--command", OURS)[1])
        self.assertEqual(self.run_cli("hooks", "uninstall", "--settings", s)[0], 0)
        self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_dry_run_writes_nothing(self):
        before = self.path.read_text()
        code, out = self.run_cli("hooks", "install", "--settings", str(self.path), "--command", OURS, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(OURS, out)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1, "no backup on a dry run")

    def test_bad_json_is_left_alone(self):
        self.path.write_text("{broken")
        code, out = self.run_cli("hooks", "install", "--settings", str(self.path), "--command", OURS)
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)
        self.assertEqual(self.path.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
