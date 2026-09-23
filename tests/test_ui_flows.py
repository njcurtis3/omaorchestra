"""Drive the whole app window like a user: offscreen, against a real
throwaway daemon, with simulated clicks and keys. Any QML warning fails."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, QPointF, Qt, QtMsgType, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: F401 (registers the types PySide converts)
    from PySide6.QtTest import QTest
    from qt_app import application
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def spin(ms=50):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        spin()
    return condition()


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class UiFlowTest(unittest.TestCase):
    def setUp(self):
        from omaorchestra import client
        from omaorchestra.app import backend
        self.client = client
        application()
        self.warnings = []
        self.previous_handler = qInstallMessageHandler(
            lambda mode, ctx, msg: mode != QtMsgType.QtDebugMsg and self.warnings.append(msg))

        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.config_path = tmp / "config.toml"
        self.config_path.write_text("# test settings\n[notifications]\nwaiting = false\nfinished_after = 0\n")
        self.env = mock.patch.dict(os.environ, {
            "OMAORCHESTRA_SOCKET": str(tmp / "o.sock"), "OMAORCHESTRA_STATE_DIR": str(tmp / "state"),
            "OMAORCHESTRA_CONFIG": str(self.config_path), "XDG_STATE_HOME": str(tmp / "xdg-state"),
        })
        self.env.start()
        env = dict(os.environ)
        env.pop("JOURNAL_STREAM", None)
        self.daemon_log = open(tmp / "daemon.log", "w+")
        self.daemon = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env, stderr=self.daemon_log)
        self.assertTrue(wait_for(lambda: (tmp / "o.sock").exists()), "daemon did not start")

        self.theme, self.sessions, self.settings = backend.Theme(), backend.Sessions(), backend.Settings()
        self.engine = QQmlApplicationEngine()
        ctx = self.engine.rootContext()
        for name, value in (("theme", self.theme), ("sessions", self.sessions), ("settings", self.settings),
                            ("fontFamily", "monospace"), ("appVersion", "test"), ("initialSession", "")):
            ctx.setContextProperty(name, value)
        self.engine.load(QUrl.fromLocalFile(str(ROOT / "src" / "omaorchestra" / "app" / "qml" / "Main.qml")))
        self.window = self.engine.rootObjects()[0]
        self.sessions.start()
        self.assertTrue(wait_for(lambda: self.sessions.connected), "app did not connect")

    def tearDown(self):
        for root in self.engine.rootObjects():
            root.close()
            root.deleteLater()
        spin()
        self.daemon.terminate()
        self.daemon.wait(timeout=5)
        self.daemon_log.close()
        self.env.stop()
        self.tmp.cleanup()
        qInstallMessageHandler(self.previous_handler)

    # ---------------------------------------------------------- helpers
    def find(self, name, item=None):
        item = item or self.window.property("contentItem")
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = self.find(name, child)
            if found:
                return found
        return None

    def shown(self, name):
        item = self.find(name)
        return item is not None and item.isVisible()

    def click(self, name):
        item = self.find(name)
        self.assertIsNotNone(item, f"no item {name}")
        self.assertTrue(item.isVisible(), f"{name} is not visible")
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        spin()

    def add_session(self, sid, status, cwd, **extra):
        self.client.request({"cmd": "update", "session_id": sid, "agent": "claude", "status": status,
                             "cwd": cwd, **extra})

    def page(self):
        return self.find("detail").parentItem()

    # ---------------------------------------------------------- flows
    def test_sessions_detail_filters_and_settings(self):
        # Sessions reported to the daemon appear live.
        self.add_session("w1", "needs-input", "/tmp/website", message="Allow Bash?", model="claude-opus-5-5")
        self.add_session("k1", "working", "/tmp/tries", branch="feature/retry")
        self.assertTrue(wait_for(lambda: self.shown("row-w1") and self.shown("row-k1")), "rows did not appear")

        # Click a row: its details open. Escape goes back.
        self.click("row-w1")
        self.assertTrue(wait_for(lambda: self.shown("detail")))
        self.assertEqual(self.page().property("selectedId"), "w1")
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertTrue(wait_for(lambda: not self.shown("detail") and self.shown("row-w1")))

        # A session that ends while its details are open shows as ended.
        self.click("row-k1")
        self.client.request({"cmd": "remove", "session_id": "k1"})
        self.assertTrue(wait_for(lambda: self.find("detail").property("gone") is True))
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.add_session("k1", "working", "/tmp/tries")
        self.assertTrue(wait_for(lambda: self.shown("row-k1")))

        # Filter chips and the search box.
        self.click("filter-needs-input")
        self.assertTrue(wait_for(lambda: self.shown("row-w1") and not self.shown("row-k1")))
        self.click("filter-all")
        self.click("search")
        for ch in "tries":  # keyClicks only takes widgets; type into the window key by key
            QTest.keyClick(self.window, getattr(Qt.Key, f"Key_{ch.upper()}"))
        self.assertTrue(wait_for(lambda: self.shown("row-k1") and not self.shown("row-w1")))
        self.assertEqual(len(self.page().property("shown")), 1)

        # Settings: flip a switch and save; the file changes (comments kept)
        # and the daemon reloads.
        self.click("nav-settings")
        self.assertTrue(wait_for(lambda: self.shown("setting-daemon.verbose")))
        self.click("setting-daemon.verbose")
        self.click("settings-save")
        text = self.config_path.read_text()
        self.assertIn("verbose = true", text)
        self.assertIn("# test settings", text)
        self.daemon_log.flush()
        self.assertTrue(wait_for(lambda: "verbose=True" in (Path(self.tmp.name) / "daemon.log").read_text()),
                        "daemon did not reload")

        self.assertEqual(self.warnings, [])

    def test_reconnects_after_the_daemon_restarts(self):
        self.add_session("w1", "idle", "/tmp/a")
        self.assertTrue(wait_for(lambda: self.shown("row-w1")))
        self.daemon.terminate()
        self.daemon.wait(timeout=5)
        self.assertTrue(wait_for(lambda: not self.sessions.connected), "did not notice the daemon going")
        env = dict(os.environ)
        env.pop("JOURNAL_STREAM", None)
        self.daemon = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env, stderr=self.daemon_log)
        self.assertTrue(wait_for(lambda: self.sessions.connected, timeout=10), "did not reconnect")
        self.assertTrue(wait_for(lambda: self.shown("row-w1")), "sessions not shown after reconnecting")
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
