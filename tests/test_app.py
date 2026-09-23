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
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from omaorchestra.app import theme as theme_file

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from qt_app import application
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

OMARCHY_COLORS = """mode = "dark"
accent = "#509475"
selection = "#32473B"
muted = "#53685B"
background = "#111c18"
lighter_background = "#23372B"
foreground = "#C1C497"
red = "#FF5345"
"""


class PaletteTest(unittest.TestCase):
    def test_maps_an_omarchy_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "colors.toml"
            path.write_text(OMARCHY_COLORS)
            p = theme_file.load(path)
        self.assertEqual(p, {"background": "#111c18", "surface": "#23372B", "foreground": "#C1C497",
                             "accent": "#509475", "muted": "#53685B", "selection": "#32473B",
                             "urgent": "#FF5345", "mode": "dark"})

    def test_missing_or_broken_theme_uses_defaults(self):
        self.assertEqual(theme_file.load("/nonexistent/colors.toml"), theme_file.DEFAULTS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "colors.toml"
            path.write_text("not = [toml")
            self.assertEqual(theme_file.load(path), theme_file.DEFAULTS)

    def test_bad_values_fall_back_per_role(self):
        p = theme_file.palette({"background": "blue", "foreground": "#abc", "mode": "sepia"})
        self.assertEqual((p["background"], p["foreground"], p["mode"]),
                         (theme_file.DEFAULTS["background"], "#abc", "dark"))


def wait_for(condition, timeout=5.0):
    loop = QEventLoop()
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return condition()


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class QtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_theme_reloads_when_the_file_changes(self):
        from omaorchestra.app.backend import Theme
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "theme" / "colors.toml"
            path.parent.mkdir()
            path.write_text(OMARCHY_COLORS)
            t = Theme(path)
            seen = []
            t.changed.connect(lambda: seen.append(t.background))
            self.assertEqual(t.background, "#111c18")
            # Replace the file the way a theme switch might.
            tmp_file = path.with_suffix(".new")
            tmp_file.write_text(OMARCHY_COLORS.replace("#111c18", "#000000"))
            os.replace(tmp_file, path)
            self.assertTrue(wait_for(lambda: seen), "no change signal")
            self.assertEqual(t.background, "#000000")

    def test_sessions_follow_snapshot_and_events(self):
        from omaorchestra.app.backend import Sessions
        s = Sessions()
        s._on_snapshot([{"id": "a", "status": "working"}, {"id": "b", "status": "needs-input"}])
        self.assertEqual((s.connected, s.total, s.waiting, s.working, s.idle), (True, 2, 1, 1, 0))
        s._on_event({"event": "session", "session": {"id": "a", "status": "idle"}})
        s._on_event({"event": "removed", "id": "b", "reason": "dismissed"})
        self.assertEqual((s.total, s.waiting, s.idle), (1, 0, 1))
        s._on_lost()
        self.assertFalse(s.connected)

    def test_second_instance_activates_the_first(self):
        from omaorchestra.app import instance
        name = f"omaorchestra-test-{os.getpid()}"
        activated = []
        server = instance.listen(activated.append, name=name)
        try:
            self.assertTrue(instance.ask_running_instance(name))
            self.assertTrue(wait_for(lambda: activated))
            self.assertTrue(instance.ask_running_instance(name, session="a808f7f3"))
            self.assertTrue(wait_for(lambda: len(activated) == 2))
        finally:
            server.close()
        self.assertEqual(activated, ["", "a808f7f3"])
        self.assertFalse(instance.ask_running_instance(name))


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class CheckCommandTest(unittest.TestCase):
    def run_check(self, env):
        out = subprocess.run([str(ROOT / "bin" / "omaorchestra"), "app", "--check"], env=env,
                             capture_output=True, text=True, timeout=30)
        return out.returncode, json.loads(out.stdout), out.stderr

    def test_check_against_a_throwaway_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "OMAORCHESTRA_SOCKET": str(Path(tmp) / "o.sock"),
                   "OMAORCHESTRA_STATE_DIR": str(Path(tmp) / "state"),
                   "OMAORCHESTRA_CONFIG": str(Path(tmp) / "none.toml"), "QT_QPA_PLATFORM": "offscreen"}
            code, result, err = self.run_check(env)
            self.assertEqual(code, 1)
            self.assertEqual((result["loaded"], result["connected"]), (True, False))

            d = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env, stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    if Path(env["OMAORCHESTRA_SOCKET"]).exists():
                        break
                    time.sleep(0.1)
                code, result, err = self.run_check(env)
            finally:
                d.terminate()
                d.wait(timeout=5)
            self.assertEqual(code, 0, err)
            self.assertEqual((result["connected"], result["window"]), (True, "omaorchestra"))
            self.assertNotIn("qml", err.lower(), "QML warnings while loading")


if __name__ == "__main__":
    unittest.main()
