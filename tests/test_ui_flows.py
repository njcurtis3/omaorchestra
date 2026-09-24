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
            "OMAORCHESTRA_WORKTREES": str(tmp / "worktrees"),
            "OMAORCHESTRA_PROVIDERS": str(tmp / "providers.json"),
        })
        self.env.start()
        env = dict(os.environ)
        env.pop("JOURNAL_STREAM", None)
        self.daemon_log = open(tmp / "daemon.log", "w+")
        self.daemon = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env, stderr=self.daemon_log)
        self.assertTrue(wait_for(lambda: (tmp / "o.sock").exists()), "daemon did not start")

        self.theme, self.sessions, self.settings = backend.Theme(), backend.Sessions(), backend.Settings()
        self.worktrees = backend.Worktrees()
        self.queue = backend.Queue(self.sessions)
        self.providers = backend.Providers()
        self.spend = backend.Spend(self.sessions)
        self.engine = QQmlApplicationEngine()
        ctx = self.engine.rootContext()
        for name, value in (("theme", self.theme), ("sessions", self.sessions), ("settings", self.settings),
                            ("worktrees", self.worktrees), ("queue", self.queue), ("providerList", self.providers), ("spend", self.spend),
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
        spin(80)  # let the layout settle (a row that just appeared may still move)
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
        self.assertTrue(wait_for(lambda: self.shown("detail")), 'self.shown("detail")')
        self.assertEqual(self.page().property("selectedId"), "w1")
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertTrue(wait_for(lambda: not self.shown("detail") and self.shown("row-w1")), 'not self.shown("detail") and self.shown("row-w1")')

        # A session that ends while its details are open shows as ended.
        self.click("row-k1")
        self.client.request({"cmd": "remove", "session_id": "k1"})
        self.assertTrue(wait_for(lambda: self.find("detail").property("gone") is True), 'self.find("detail").property("gone") is True')
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.add_session("k1", "working", "/tmp/tries")
        self.assertTrue(wait_for(lambda: self.shown("row-k1")), 'self.shown("row-k1")')

        # Filter chips and the search box.
        self.click("filter-needs-input")
        self.assertTrue(wait_for(lambda: self.shown("row-w1") and not self.shown("row-k1")), 'self.shown("row-w1") and not self.shown("row-k1")')
        self.click("filter-all")
        self.click("search")
        for ch in "tries":  # keyClicks only takes widgets; type into the window key by key
            QTest.keyClick(self.window, getattr(Qt.Key, f"Key_{ch.upper()}"))
        self.assertTrue(wait_for(lambda: self.shown("row-k1") and not self.shown("row-w1")), 'self.shown("row-k1") and not self.shown("row-w1")')
        self.assertEqual(len(self.page().property("shown")), 1)

        # Settings: flip a switch and save; the file changes (comments kept)
        # and the daemon reloads.
        self.click("nav-settings")
        self.assertTrue(wait_for(lambda: self.shown("setting-daemon.verbose")), 'self.shown("setting-daemon.verbose")')
        self.click("setting-daemon.verbose")
        self.click("settings-save")
        text = self.config_path.read_text()
        self.assertIn("verbose = true", text)
        self.assertIn("# test settings", text)
        self.daemon_log.flush()
        self.assertTrue(wait_for(lambda: "verbose=True" in (Path(self.tmp.name) / "daemon.log").read_text()),
                        "daemon did not reload")

        self.assertEqual(self.warnings, [])

    def test_new_task_form_launches_and_opens_the_session(self):
        from omaorchestra import launch
        calls = []

        def fake_run(task, cwd, model=None, permission_mode=None, worktree=None, provider=None, **kw):
            calls.append((task, cwd, model, permission_mode, worktree))
            # What a real launch does first: register the session.
            self.add_session("new-1", "working", cwd, title=task, launching=True)
            return {"id": "new-1", "tracked": True, "worktree": None, "note": ""}

        with mock.patch.object(launch, "run", fake_run):
            QTest.keyClick(self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
            self.assertTrue(wait_for(lambda: self.shown("task-prompt")), "Ctrl+N did not open the form")
            self.click("task-prompt")
            for key in ("Key_F", "Key_I", "Key_X", "Key_Space", "Key_I", "Key_T"):
                QTest.keyClick(self.window, getattr(Qt.Key, key))
            folder = self.find("task-folder")
            folder.setProperty("text", "/definitely/not/a/folder")
            spin()
            self.assertFalse(self.find("task-launch").property("enabled"), "launch allowed with a missing folder")
            folder.setProperty("text", self.tmp.name)
            spin()
            self.assertTrue(self.find("task-launch").property("enabled"))
            model_box = self.find("task-model")
            values = [c["value"] for c in model_box.property("model")]
            model_box.setProperty("currentIndex", values.index("opus"))
            self.find("task-remember-model").setProperty("checked", True)
            spin()
            self.click("task-launch")
            self.assertTrue(wait_for(lambda: self.shown("detail")), "did not open the new session")
        # Not a git repository, so no worktree even though it is on by default.
        self.assertEqual(calls, [("fix it", self.tmp.name, "opus", None, False)])
        from omaorchestra import modeldefaults
        self.assertEqual(modeldefaults.for_folder(self.tmp.name), ("opus", "folder"), "not remembered")
        self.assertEqual(self.page().property("selectedId"), "new-1")
        self.assertEqual(self.find("task-prompt").property("text"), "", "form not cleared after launching")
        self.assertEqual(self.warnings, [])

    def test_worktrees_page_merge_and_remove(self):
        from PySide6.QtCore import QMetaObject, QObject
        from omaorchestra import worktrees
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()

        def git(cwd, *args):
            subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("a\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "first")
        record = worktrees.create(repo, "Add b", "abcdef123")
        (Path(record["path"]) / "b.txt").write_text("b\n")
        git(record["path"], "add", ".")
        git(record["path"], "commit", "-qm", "add b")

        self.click("nav-worktrees")
        name = "worktree-" + record["branch"]
        self.assertTrue(wait_for(lambda: self.shown(name)), "worktree not listed")
        confirm = self.window.findChild(QObject, "worktree-confirm")

        self.click("merge-" + record["branch"])
        self.assertTrue(wait_for(lambda: confirm.property("opened")), 'confirm.property("opened")')
        QMetaObject.invokeMethod(confirm, "accept")
        self.assertTrue(wait_for(lambda: (repo / "b.txt").exists()), "merge did not happen")

        self.click("remove-" + record["branch"])
        self.assertTrue(wait_for(lambda: confirm.property("opened")), 'confirm.property("opened")')
        QMetaObject.invokeMethod(confirm, "accept")
        self.assertTrue(wait_for(lambda: not self.shown(name)), "worktree still listed after removing")
        self.assertFalse(Path(record["path"]).exists())
        self.assertEqual(self.warnings, [])

    def test_queue_from_the_form_then_pause_resume_cancel(self):
        # Hold first, so nothing is actually launched during the test.
        self.click("nav-queue")
        self.click("queue-hold")
        self.assertTrue(wait_for(lambda: self.queue.held), "queue not held")

        QTest.keyClick(self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(wait_for(lambda: self.shown("task-prompt")), "form not open")
        self.click("task-prompt")
        for key in ("Key_L", "Key_A", "Key_T", "Key_E", "Key_R"):
            QTest.keyClick(self.window, getattr(Qt.Key, key))
        self.find("task-folder").setProperty("text", self.tmp.name)
        spin()
        self.click("task-queue")
        self.assertTrue(wait_for(lambda: self.shown("queued-0")), "did not switch to the queue with the task")
        (task,) = self.queue.tasks
        self.assertEqual((task["task"], task["state"], task["cwd"]), ("later", "pending", self.tmp.name))
        self.assertTrue(task["path"], "the environment PATH travels with the task")

        self.click("queued-pause-0")
        self.assertTrue(wait_for(lambda: self.queue.tasks and self.queue.tasks[0]["state"] == "paused"), "not paused")
        self.click("queued-pause-0")
        self.assertTrue(wait_for(lambda: self.queue.tasks and self.queue.tasks[0]["state"] == "pending"), "not resumed")
        self.click("queued-cancel-0")
        self.assertTrue(wait_for(lambda: not self.queue.tasks and not self.shown("queued-0")), "not cancelled")
        self.assertEqual(self.warnings, [])

    def test_providers_page_add_and_test(self):
        self.click("nav-providers")
        kinds = self.find("provider-kind")
        self.assertTrue(wait_for(lambda: kinds is not None and kinds.isVisible()), "providers page not shown")
        kinds.setProperty("currentIndex", [k["value"] for k in self.providers.kinds].index("ollama"))
        self.find("provider-id").setProperty("text", "local")
        self.find("provider-url").setProperty("text", "http://127.0.0.1:9")  # nothing listens there
        spin()
        self.click("provider-add")
        self.assertTrue(wait_for(lambda: self.shown("provider-local")), "provider not listed")
        self.click("provider-test-local")
        result = lambda: self.find("provider-result-local")  # noqa: E731
        self.assertTrue(wait_for(lambda: result() is not None and "cannot reach" in result().property("text"), timeout=20),
                        "test result not shown")
        self.assertEqual(self.warnings, [])

    def test_usage_page_shows_the_report(self):
        self.add_session("u1", "idle", "/tmp/usage-demo")
        self.click("nav-usage")
        today = lambda: self.find("usage-today")  # noqa: E731
        self.assertTrue(wait_for(lambda: today() is not None and "Provider spend: $0.00" in today().property("text"),
                                 timeout=10), "report not shown")
        self.assertEqual(self.warnings, [])

    def test_reconnects_after_the_daemon_restarts(self):
        self.add_session("w1", "idle", "/tmp/a")
        self.assertTrue(wait_for(lambda: self.shown("row-w1")), 'self.shown("row-w1")')
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
