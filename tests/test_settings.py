import asyncio
import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from omaorchestra import config, daemon
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry

try:
    from PySide6.QtCore import QEventLoop, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from qt_app import application
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

COMMENTED = """# my settings
[daemon]
# check often
prune_interval = 30  # seconds

[agents]
enabled = ["claude"]  # only claude
"""


class EditTextTest(unittest.TestCase):
    def test_keeps_comments_and_order(self):
        out = config.edit_text(COMMENTED, {"daemon": {"prune_interval": 60}, "agents": {"enabled": []}})
        self.assertEqual(out, COMMENTED.replace("= 30  #", "= 60  #").replace('["claude"]  #', "[]  #"))

    def test_adds_keys_to_their_section_and_new_sections_at_the_end(self):
        out = config.edit_text(COMMENTED, {"daemon": {"verbose": True}, "notifications": {"waiting": False}})
        self.assertIn("prune_interval = 30  # seconds\nverbose = true\n", out)
        self.assertTrue(out.endswith("\n[notifications]\nwaiting = false\n"))
        self.assertEqual(tomllib.loads(out)["daemon"], {"prune_interval": 30, "verbose": True})

    def test_refuses_multiline_values(self):
        with self.assertRaises(config.ConfigError):
            config.edit_text('[agents]\nenabled = [\n  "claude",\n]\n', {"agents": {"enabled": []}})


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_file(self):
        result = config.save({"notifications": {"finished_after": 300}}, self.path)
        self.assertEqual(result["notifications"]["finished_after"], 300)
        self.assertTrue(self.path.read_text().startswith("# omaorchestra settings"))
        self.assertFalse(self.path.with_name("config.toml.bak").exists())

    def test_backup_and_validation(self):
        self.path.write_text(COMMENTED)
        config.save({"daemon": {"prune_interval": 45}}, self.path)
        self.assertEqual(self.path.with_name("config.toml.bak").read_text(), COMMENTED)
        before = self.path.read_text()
        for bad in ({"daemon": {"prune_interval": 1}}, {"daemon": {"nope": 1}}, {"agents": {"enabled": ["gpt"]}}):
            with self.assertRaises(config.ConfigError):
                config.save(bad, self.path)
        self.assertEqual(self.path.read_text(), before, "a rejected save must not write")

    def test_parse_value(self):
        self.assertIs(config.parse_value("daemon", "verbose", "on"), True)
        self.assertEqual(config.parse_value("daemon", "prune_interval", "60"), 60)
        self.assertEqual(config.parse_value("agents", "enabled", "claude, "), ["claude"])
        for section, key, text in (("daemon", "verbose", "maybe"), ("daemon", "prune_interval", "x"), ("x", "y", "1")):
            with self.assertRaises(config.ConfigError):
                config.parse_value(section, key, text)

    def test_describe_covers_the_schema(self):
        described = config.describe(config.defaults())
        names = {(f["section"], f["key"]) for s in described for f in s["fields"]}
        self.assertEqual(names, {(s, k) for s, keys in config.SCHEMA.items() for k in keys})
        spin = next(f for s in described for f in s["fields"] if f["key"] == "prune_interval")
        self.assertEqual((spin["kind"], spin["min"], spin["max"], spin["value"]), ("int", 5, 3600, 30))


class ReloadTest(unittest.TestCase):
    def test_reload_applies_or_keeps_old_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            with mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": str(path)}):
                class N:
                    settings = None
                d = daemon.Daemon(Registry(Path(tmp) / "r.json"), notifier=N())

                async def scenario():
                    d.start_pruner()
                    first = d.pruner
                    path.write_text("[daemon]\nprune_interval = 10\n[notifications]\nfinished_after = 0\n")
                    ok = d.handle({"cmd": "reload"})
                    replaced = d.pruner is not first
                    path.write_text("[daemon]\nprune_interval = 1\n")
                    bad = d.handle({"cmd": "reload"})
                    d.pruner.cancel()
                    return ok, replaced, bad

                ok, replaced, bad = asyncio.run(scenario())
        self.assertEqual(ok, {"ok": True})
        self.assertTrue(replaced, "a new interval restarts the pruner")
        self.assertFalse(bad["ok"])
        self.assertIn("prune_interval", bad["error"])
        self.assertEqual(d.settings["daemon"]["prune_interval"], 10, "a bad file keeps the running settings")
        self.assertEqual(d.notifier.settings["finished_after"], 0)

    def test_sighup_reloads_a_running_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "OMAORCHESTRA_SOCKET": str(Path(tmp) / "o.sock"),
                   "OMAORCHESTRA_STATE_DIR": str(Path(tmp) / "state"),
                   "OMAORCHESTRA_CONFIG": str(Path(tmp) / "config.toml")}
            env.pop("JOURNAL_STREAM", None)
            proc = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env,
                                    stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(50):
                    if Path(env["OMAORCHESTRA_SOCKET"]).exists():
                        break
                    time.sleep(0.1)
                Path(env["OMAORCHESTRA_CONFIG"]).write_text("[notifications]\nfinished_after = 600\n")
                proc.send_signal(signal.SIGHUP)
                time.sleep(0.5)
                proc.send_signal(signal.SIGTERM)
                _, err = proc.communicate(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()
        self.assertIn("config reloaded", err)
        self.assertIn("finished_after=600", err)


class ConfigSetCliTest(unittest.TestCase):
    def test_set_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            env = {"OMAORCHESTRA_CONFIG": str(path), "OMAORCHESTRA_SOCKET": str(Path(tmp) / "none.sock")}
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = main(["config", "set", "notifications.finished_after", "300"])
                bad = main(["config", "set", "notifications.finished_after", "lots"])
            self.assertEqual((code, bad), (0, 1))
            self.assertEqual(config.load(path)["notifications"]["finished_after"], 300)
        self.assertIn("notifications.finished_after = 300", out.getvalue())
        self.assertIn("not running", out.getvalue())


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class SettingsScreenTest(unittest.TestCase):
    def test_backend_save_and_render(self):
        from omaorchestra.app.backend import Settings, Theme
        application()
        warnings = []
        previous = qInstallMessageHandler(lambda mode, ctx, msg: warnings.append(msg))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.toml"
                path.write_text(COMMENTED)
                with mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": str(path),
                                                  "OMAORCHESTRA_SOCKET": str(Path(tmp) / "none.sock")}):
                    settings = Settings()
                    theme = Theme(Path(tmp) / "none" / "colors.toml")
                    self.assertEqual(settings.save({"daemon.prune_interval": 90.0, "daemon.verbose": True}), "")
                    self.assertIn("prune_interval = 90  # seconds", path.read_text())
                    self.assertIn("# check often", path.read_text())
                    rejected = settings.save({"daemon.prune_interval": 1})
                    self.assertIn("prune_interval", rejected)
                    self.assertIn("prune_interval = 90", path.read_text(), "a rejected save must not write")
                    engine = QQmlEngine()
                    engine.rootContext().setContextProperty("theme", theme)
                    engine.rootContext().setContextProperty("settings", settings)
                    qml = ROOT / "src" / "omaorchestra" / "app" / "qml" / "SettingsPage.qml"
                    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml)))
                    page = component.createWithInitialProperties({"width": 900, "height": 700})
                    self.assertIsNotNone(page, component.errorString())
                    loop = QEventLoop()
                    QTimer.singleShot(100, loop.quit)
                    loop.exec()
                    self.assertEqual(len(settings.sections), len(config.SCHEMA))
                    page.deleteLater()
        finally:
            qInstallMessageHandler(previous)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
