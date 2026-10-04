"""Drive the whole app window like a user: offscreen, against a real
throwaway daemon, with simulated clicks and keys. Any QML warning fails."""

import json
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
            "OMAORCHESTRA_MCP": str(tmp / "mcp.json"), "OMAORCHESTRA_AGENT_HOME": str(tmp / "agent-home"),
        })
        self.env.start()
        env = self.daemon_env()
        self.daemon_log = open(tmp / "daemon.log", "w+")
        self.daemon = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env, stderr=self.daemon_log)
        self.assertTrue(wait_for(lambda: (tmp / "o.sock").exists()), "daemon did not start")

        self.theme, self.sessions, self.settings = backend.Theme(), backend.Sessions(), backend.Settings()
        self.worktrees = backend.Worktrees()
        self.queue = backend.Queue(self.sessions)
        self.schedules = backend.Schedules(self.sessions)
        self.away = backend.Away(self.sessions)
        self.history = backend.History(self.sessions)
        self.providers = backend.Providers()
        self.spend = backend.Spend(self.sessions)
        self.mcp = backend.Mcp(self.sessions)
        self.fleets = backend.Fleets(self.sessions)
        self.engine = QQmlApplicationEngine()
        ctx = self.engine.rootContext()
        for name, value in (("theme", self.theme), ("sessions", self.sessions), ("settings", self.settings),
                            ("worktrees", self.worktrees), ("queue", self.queue), ("schedules", self.schedules), ("awayMode", self.away), ("sessionHistory", self.history), ("providerList", self.providers), ("spend", self.spend), ("mcp", self.mcp), ("fleets", self.fleets),
                            ("fontFamily", "monospace"), ("appVersion", "test"), ("initialSession", ""),
                            ("initialFleet", "")):
            ctx.setContextProperty(name, value)
        self.engine.load(QUrl.fromLocalFile(str(ROOT / "src" / "omaorchestra" / "app" / "qml" / "Main.qml")))
        self.window = self.engine.rootObjects()[0]
        # Shortcuts (Ctrl+N) fire only in the active window, and offscreen a
        # new window becomes active some time after it is shown.
        self.window.requestActivate()
        self.assertTrue(wait_for(self.window.isActive), "window did not become active")
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
    def daemon_env(self):
        env = dict(os.environ)
        env.pop("JOURNAL_STREAM", None)
        # Keep the test daemon off this desktop's lock screen, input and keyring.
        for name in ("WAYLAND_DISPLAY", "OMARCHY_PATH"):
            env.pop(name, None)
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(Path(self.tmp.name) / "no-bus")
        return env

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
        self.scroll_to(item)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        spin()

    def scroll_to(self, item):
        """Scroll the view it is in until the item shows, as a user would
        before clicking it (a tall form scrolls)."""
        flick = item.parentItem()
        while flick is not None and flick.property("contentY") is None:
            flick = flick.parentItem()
        if flick is None:
            return
        below = item.mapToScene(QPointF(0, item.height())).y() - flick.mapToScene(QPointF(0, flick.height())).y()
        if below > 0:
            flick.setProperty("contentY", flick.property("contentY") + below + 16)
            spin()

    def choose(self, name, steps):
        """Pick the entry `steps` below the current one in a dropdown, with the
        keyboard (the open list highlights whatever the mouse is over)."""
        self.click(name)  # focuses it and opens the list
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        spin()
        for _ in range(steps):
            QTest.keyClick(self.window, Qt.Key.Key_Down)
            spin()

    def add_session(self, sid, status, cwd, **extra):
        self.client.request({"cmd": "update", "session_id": sid, "agent": "claude", "status": status,
                             "cwd": cwd, **extra})

    def page(self):
        return self.find("detail").parentItem()

    # ---------------------------------------------------------- flows
    def test_the_logo_heads_the_navigation(self):
        logo = self.find("logo")
        # Loaded and drawn.
        self.assertTrue(wait_for(lambda: logo.property("paintedWidth") > 100), "the logo was not drawn")
        self.assertTrue(logo.isVisible())
        self.assertEqual(logo.width(), 168)  # the navigation's width inside its margins
        self.assertLess(logo.mapToScene(QPointF(0, 0)).y(), 20)

    def test_nothing_overflows_the_smallest_window(self):
        # A long title, branch and model, then every page at the minimum size:
        # no text reaches past the window's right edge (rows elide or wrap,
        # button rows wrap), and the navigation is down to its glyphs.
        self.add_session("n1", "needs-input", "/tmp/a-project-with-a-rather-long-name",
                         branch="feature/a-branch-name-long-enough-to-crowd-the-row",
                         model="claude-opus-5-5", title="A task title long enough to fill the row twice over " * 2,
                         message="Claude needs your permission to use Bash: " + "x" * 120)
        self.assertTrue(wait_for(lambda: self.shown("row-n1")), "row did not appear")
        # Wide first, with every page laid out, then shrunk: a layout that sized
        # itself from its own width could hold the page wide.
        self.window.setWidth(1600)
        for page in [p["id"] for p in self.window.property("pages").toVariant()]:
            self.window.setProperty("page", page)
            spin(50)
        self.window.setWidth(640)
        self.window.setHeight(420)
        spin(200)
        self.assertLess(self.find("nav").width(), 100)
        # A row's four icons fold into one menu.
        self.window.setProperty("page", "sessions")
        spin(150)
        self.assertTrue(self.shown("row-menu-n1"), "no row menu in a narrow window")
        width = self.window.width()

        def overflowing(item):
            if not item.isVisible():
                return []
            found = []
            if item.inherits("QQuickText") and item.property("text"):  # Label and Text
                # Text wider than its box is drawn past it (a centred line on both sides).
                spill = max(0, (item.property("contentWidth") or 0) - item.width())
                right = item.mapToScene(QPointF(item.width() + spill, 0)).x()
                if right > width + 1:
                    found.append(f"{item.property('text')[:40]!r} ends at {right:.0f}")
            for child in item.childItems():
                found += overflowing(child)
            return found

        pages = [p["id"] for p in self.window.property("pages").toVariant()]
        self.assertEqual(len(pages), 11)
        # Also in a font about as wide as CI's (12 px a character, where this
        # desktop's is often 8 or 9): a layout that only just fits here can
        # overflow there. Where the font is that wide already, once is enough.
        from PySide6.QtGui import QFontMetricsF
        font = self.window.property("font")
        wide = max(font.pixelSize(), round(font.pixelSize() * 12 / QFontMetricsF(font).horizontalAdvance("M")))
        for size in sorted({font.pixelSize(), wide}):
            font.setPixelSize(size)
            self.window.setProperty("font", font)
            for page in pages:
                self.window.setProperty("page", page)
                spin(150)
                self.assertEqual(overflowing(self.window.property("contentItem")), [],
                                 f"{page} overflows at {size}px")

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

        # Appearance: pick Light and save; the window redraws in it.
        from omaorchestra.app import theme as theme_file
        self.click("setting-appearance.mode-light")
        self.click("settings-save")
        self.assertIn('mode = "light"', self.config_path.read_text())
        self.assertTrue(wait_for(lambda: self.theme.background == theme_file.LIGHT["background"]),
                        "the app did not turn light")
        self.assertFalse(self.theme.dark)

        self.assertEqual(self.warnings, [])

    def test_new_task_form_launches_and_opens_the_session(self):
        from omaorchestra import launch
        calls = []

        def fake_run(task, cwd, model=None, permission_mode=None, worktree=None, provider=None, mcp_profile=None,
                     agent="claude", **kw):
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

    def test_schedule_a_task_then_pause_and_remove_it(self):
        self.client.request({"cmd": "queue-hold"})  # a "run now" stays queued, not launched
        QTest.keyClick(self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(wait_for(lambda: self.shown("task-prompt")), "Ctrl+N did not open the form")
        self.find("task-prompt").setProperty("text", "update the dependencies")
        self.find("task-folder").setProperty("text", self.tmp.name)
        self.click("task-schedule-open")
        self.assertTrue(wait_for(lambda: self.shown("task-when")), "Schedule… did not open")
        when = self.find("task-when")
        when.setProperty("text", "whenever")
        spin()
        self.assertFalse(self.find("task-schedule-save").property("enabled"), "a time it cannot read was allowed")
        self.assertIn("say when", self.find("task-when-check").property("text"))
        when.setProperty("text", "weekdays 09:00")
        spin()
        self.assertIn("weekdays at 09:00", self.find("task-when-check").property("text"))
        self.click("task-schedule-save")
        # Saved: the Queue page shows it, and the daemon has it.
        self.assertTrue(wait_for(lambda: self.shown("schedule-0")), "the schedule is not on the Queue page")
        (saved,) = self.client.request({"cmd": "schedule-list"})["schedules"]["schedules"]
        self.assertEqual((saved["task"], saved["whenText"], saved["items"][0]["cwd"]),
                         ("update the dependencies", "weekdays at 09:00", self.tmp.name))
        self.click("schedule-run-0")
        self.assertTrue(wait_for(lambda: len(self.queue.property("tasks")) == 1), "run now did not queue it")
        self.click("schedule-pause-0")
        self.assertTrue(wait_for(lambda: self.client.request({"cmd": "schedule-list"})
                                 ["schedules"]["schedules"][0]["paused"]), "not paused")
        self.click("schedule-remove-0")
        self.assertTrue(wait_for(lambda: not self.shown("schedule-0")), "not removed")
        self.assertEqual(self.client.request({"cmd": "schedule-list"})["schedules"]["schedules"], [])
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

    def test_away_switch_shows_while_away_matters(self):
        # Remote answers are on by default, so the switch shows with push off.
        self.assertTrue(wait_for(lambda: self.shown("away")), "away switch did not appear")
        self.config_path.write_text("[remote]\npush = false\nanswer_prompts = false\n")
        self.client.request({"cmd": "reload"})
        self.assertTrue(wait_for(lambda: not self.shown("away")), "away switch shown while it changes nothing")
        self.config_path.write_text("[remote]\npush = true\n")
        self.client.request({"cmd": "reload"})
        self.assertTrue(wait_for(lambda: self.shown("away")), "away switch did not come back")
        self.assertEqual(self.away.mode, "auto")
        self.choose("away", 1)  # Away
        self.assertTrue(wait_for(lambda: self.away.away), "not away after choosing Away")
        self.assertEqual(self.client.request({"cmd": "away"})["away"]["mode"], "on")
        self.assertEqual(self.find("away").property("currentText"), "Away")
        self.choose("away", 1)  # At the desk, one further down
        self.assertTrue(wait_for(lambda: self.away.mode == "off" and not self.away.away))
        # Changed elsewhere (the CLI, top, the bar): the dropdown follows.
        self.client.request({"cmd": "away", "mode": "auto"})
        self.assertTrue(wait_for(lambda: self.find("away").property("currentText").startswith("Auto")))
        self.assertEqual(self.warnings, [])

    def test_history_page(self):
        self.add_session("h1", "working", self.tmp.name, title="Tidy the aliases")
        self.add_session("h1", "idle", self.tmp.name)
        self.client.request({"cmd": "remove", "session_id": "h1"})
        self.assertTrue(wait_for(lambda: any(r["id"] == "h1" for r in self.history.rows), timeout=10),
                        "the ended session did not reach the history")
        self.click("nav-history")
        self.assertTrue(wait_for(lambda: self.shown("history-row-h1")), "no history row")
        self.click("history-row-h1")
        self.assertTrue(wait_for(lambda: self.shown("history-detail")), "no history detail")
        self.assertTrue(self.shown("history-resume"))
        self.click("history-tab-changes")
        spin(300)
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.assertTrue(wait_for(lambda: self.shown("history-row-h1")), "Esc did not go back")
        self.click("history-filter-crashed")
        self.assertTrue(wait_for(lambda: not self.shown("history-row-h1")), "the filter did not apply")
        self.click("history-filter-all")
        self.click("nav-usage")
        self.assertTrue(wait_for(lambda: "1 session" in (self.find("stats-summary").property("text") or "")))
        self.assertEqual(self.warnings, [])

    def test_a_chain_from_the_form(self):
        self.window.setProperty("height", 1000)  # the whole form on screen, whatever the fonts
        spin()
        self.click("nav-queue")
        self.click("queue-hold")  # nothing actually starts
        self.assertTrue(wait_for(lambda: self.queue.held), "queue not held")
        QTest.keyClick(self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(wait_for(lambda: self.shown("task-prompt")), "form not open")
        self.click("task-prompt")
        for key in ("Key_F", "Key_I", "Key_X"):
            QTest.keyClick(self.window, getattr(Qt.Key, key))
        self.find("task-folder").setProperty("text", self.tmp.name)
        self.click("task-then-open")
        self.assertTrue(wait_for(lambda: self.shown("task-then")), "no Then… box")
        self.click("task-then")
        for key in ("Key_T", "Key_E", "Key_S", "Key_T"):
            QTest.keyClick(self.window, getattr(Qt.Key, key))
        spin()
        self.assertEqual(self.find("task-launch").property("text"), "Start chain")
        self.assertFalse(self.shown("task-queue"))
        self.click("task-launch")
        self.assertTrue(wait_for(lambda: len(self.queue.tasks) == 2), "the chain was not queued")
        first, second = self.queue.tasks
        self.assertEqual((first["task"], first["state"]), ("fix", "pending"))
        self.assertEqual((second["task"], second["state"], second["after"], second["same_worktree"]),
                         ("test", "waiting", first["id"], True))
        self.assertTrue(wait_for(lambda: self.shown("queued-1")), "the queue does not show it")
        self.assertFalse(self.shown("queued-run-1"), "a waiting step cannot be started early")

        # A recipe instead.
        QTest.keyClick(self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(wait_for(lambda: self.shown("task-recipe")), "no recipe choice")
        self.click("task-prompt")
        QTest.keyClick(self.window, Qt.Key.Key_X)
        self.find("task-folder").setProperty("text", self.tmp.name)
        self.choose("task-recipe", 1)  # build-then-review, the first after None
        spin()
        self.assertEqual(self.find("task-launch").property("text"), "Start chain")
        self.assertFalse(self.shown("task-then-open"), "a recipe has its own steps")
        self.click("task-launch")
        self.assertTrue(wait_for(lambda: len(self.queue.tasks) == 4), "the recipe was not queued")
        self.assertEqual([t.get("recipe") for t in self.queue.tasks[2:]], ["build-then-review"] * 2)
        self.assertTrue(self.queue.tasks[3]["review"])
        self.assertEqual(self.warnings, [])

    def test_the_omafleet_tab(self):
        import fleet_fixtures
        from omaorchestra import adapters, fleet, procs
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                                      "-m", "init"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        ids = fleet_fixtures.write_all(str(repo))
        # Answers at a gate are refused from inside an agent, and this test
        # may run inside one (the app is this process): then it checks the
        # refusal; outside (CI), it answers.
        inside = procs.under_agent(os.getpid(), {n for a in adapters.ADAPTERS.values() for n in a.process_names})
        self.client.request({"cmd": "queue-hold"})  # nothing actually starts
        self.window.setProperty("height", 1000)
        spin()
        self.click("nav-fleets")
        self.assertTrue(wait_for(lambda: self.fleets.count == len(ids)), "the runs did not load")
        self.assertTrue(self.shown("nav-badge-fleets"))
        self.assertTrue(wait_for(lambda: self.shown("fleet-run-" + ids["plan"])))

        # The plan gate: drop a slice, then approve.
        self.window.showFleet(ids["plan"][:16])
        self.assertTrue(wait_for(lambda: self.shown("card-plan")), "no plan card")
        self.assertTrue(wait_for(lambda: self.shown("graph-scout")), "no graph")
        self.click("plan-drop-s3")
        if inside:
            self.assertTrue(wait_for(lambda: "inside an agent" in self.find("run-message").property("text")))
            self.assertIsNone(fleet.load(ids["plan"]).get("dropped"))
        else:
            self.assertTrue(wait_for(lambda: fleet.load(ids["plan"]).get("dropped") == ["s3"]), "not dropped")
            self.click("card-approve")
            self.assertTrue(wait_for(lambda: fleet.load(ids["plan"])["status"] == "running"), "not approved")
            self.assertTrue(wait_for(lambda: not self.shown("card-plan")), "the card stayed")

            # Files outside a slice: accept them, with a reason.
            self.window.showFleet(ids["scope"])
            self.assertTrue(wait_for(lambda: self.shown("card-scope")), "no scope card")
            self.assertFalse(self.find("card-accept").property("enabled"), "accepted without a reason")
            self.find("card-note").setProperty("text", "the changelog goes with it")
            spin()
            self.click("card-accept")
            self.assertTrue(wait_for(lambda: fleet.load(ids["scope"])["nodes"]["builder.s1"]["scope"]["accepted"]))
            self.assertTrue(wait_for(lambda: "reviewer.s1" in fleet.load(ids["scope"])["nodes"]), "no reviewer next")

        # A builder's split, at its gate: the smaller slices on the card and under their slice.
        self.window.showFleet(ids["split"])
        self.assertTrue(wait_for(lambda: self.shown("card-split")), "no split card")
        self.assertTrue(self.shown("split-slice-s1-a"))
        self.click("run-view-board")
        self.assertTrue(wait_for(lambda: self.shown("board-s1-b")), "the smaller slices are not on the board")
        if not inside:
            self.click("card-approve")
            self.assertTrue(wait_for(lambda: "split:builder.s1" in fleet.load(ids["split"])["approved"]),
                            "split not approved")

        # The board, and a node's detail.
        self.window.showFleet(ids["merge"])
        self.assertTrue(wait_for(lambda: self.shown("card-merge")))
        self.click("run-view-board")
        self.assertTrue(wait_for(lambda: self.shown("board-s2")), "no board")
        self.click("board-s2")
        self.assertTrue(wait_for(lambda: self.shown("node-detail")), "no node detail")
        self.assertEqual(self.find("run-view").property("nodeId"), "builder.s2.2")
        self.click("run-view-report")
        self.assertTrue(wait_for(lambda: self.shown("report-role-builder")), "no report")
        self.assertIn("Sent back 1 time (1 REJECT)", self.find("report-summary").property("text"))
        self.click("nav-usage")
        self.assertTrue(wait_for(lambda: self.shown("fleet-stats")), "no fleet roles on the Usage page")
        self.click("nav-fleets")

        # A fleet node in Sessions carries a chip that opens its run.
        self.add_session("fleet-node-1", "working", str(repo), fleet=ids["running"], node="builder.s2")
        self.click("nav-sessions")
        self.assertTrue(wait_for(lambda: self.shown("fleet-chip-fleet-node-1")), "no fleet chip")
        self.click("filter-fleet")
        self.assertTrue(wait_for(lambda: not self.shown("fleet-chip-fleet-node-1")), "fleet sessions not hidden")
        self.click("filter-fleet")
        self.assertTrue(wait_for(lambda: self.shown("fleet-chip-fleet-node-1")))
        self.click("fleet-chip-fleet-node-1")
        self.assertEqual(self.window.property("page"), "fleets")

        # A task that grew moves to a fleet run, text and folder carried over; then starts.
        QTest.keyClick(self.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(wait_for(lambda: self.shown("task-prompt")))
        self.find("task-prompt").setProperty("text", "Move billing to the new provider")
        self.find("task-folder").setProperty("text", str(repo))
        spin()
        self.click("task-as-fleet")
        self.assertTrue(wait_for(lambda: self.shown("fleet-goal")), "no fleet form")
        self.assertEqual(self.find("fleet-goal").property("text"), "Move billing to the new provider")
        self.assertEqual(self.find("fleet-folder").property("text"), str(repo))
        self.assertTrue(self.shown("fleet-hint"), "no word about the stop rule for a short goal")
        before = self.fleets.count
        self.click("fleet-start")
        self.assertTrue(wait_for(lambda: self.fleets.count == before + 1), "the run did not start")
        self.assertTrue(wait_for(lambda: self.shown("run-view")))
        new_run = next(r for r in fleet.runs() if r["goal"] == "Move billing to the new provider")

        # The queue shows a run's nodes as one row that opens it.
        self.click("nav-queue")
        self.assertTrue(wait_for(lambda: self.shown("queued-fleet-" + new_run["id"])), "no fleet row in the queue")
        self.click("queued-open-fleet")
        self.assertEqual(self.window.property("page"), "fleets")
        self.assertEqual(self.warnings, [])

    def test_outside_runs_are_shown_read_only(self):
        runs = Path(self.tmp.name) / "graph_agents" / ".graph" / "runs" / "2026-09-20-fleet-gaps"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({
            "run_id": "2026-09-20-fleet-gaps", "goal": "Close three gaps", "status": "blocked",
            "architect": {"shape": "single-loop", "plan": [{"slice": "s1", "intent": "Diagnose", "files": ["a.py"]}]},
            "builders": {"s1": {"status": "done", "branch": "s1-x"}},
            "reviews": {"s1": {"verdict": "PASS", "summary": "fine"}}}))
        claude = Path(self.tmp.name) / "claude"
        (claude / "teams" / "session-abc").mkdir(parents=True)
        (claude / "teams" / "session-abc" / "config.json").write_text(json.dumps({"members": [
            {"name": "team-lead", "agentType": "team-lead"}, {"name": "tester", "agentType": "reviewer"}]}))
        (claude / "tasks" / "session-abc").mkdir(parents=True)
        (claude / "tasks" / "session-abc" / "1.json").write_text(json.dumps({"id": "1", "subject": "Run the tests",
                                                                            "status": "in_progress"}))
        with open(self.config_path, "a") as f:
            f.write(f'[fleets]\nwatch = ["{Path(self.tmp.name) / "graph_agents"}"]\n')
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(claude)}):
            self.click("nav-fleets")
            self.assertTrue(wait_for(lambda: self.fleets.outsideCount == 2), "outside runs not read")
            self.assertTrue(wait_for(lambda: self.shown("section-Outside (read-only)")), "no outside section")
            self.window.showFleet("graph_agents:2026-09-20")
            self.assertTrue(wait_for(lambda: self.shown("run-outside")), "no read-only banner")
            self.assertFalse(self.shown("card-held"), "an outside run offers answers")
            self.assertFalse(self.shown("run-cancel"))
            self.click("run-view-board")
            self.assertTrue(wait_for(lambda: self.shown("board-s1")))
            result = self.fleets.cancel("graph_agents:2026-09-20-fleet-gaps")
            self.assertIn("only watches", result["error"])
            self.window.showFleet("team:session-abc")
            self.assertTrue(wait_for(lambda: self.shown("team-task-1")), "no team view")
            self.assertFalse(self.shown("run-graph"))
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

    def test_mcp_page_checks_servers_and_saves_a_profile(self):
        from omaorchestra.mcp import registry
        home = Path(self.tmp.name) / "agent-home"
        home.mkdir()
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"self": {
            "command": str(ROOT / "bin" / "omaorchestra"), "args": ["mcp", "serve"]}}}))
        registry.add("db", {"transport": "stdio", "command": "db-mcp", "args": []}, [], {})
        self.click("nav-mcp")
        self.assertTrue(wait_for(lambda: self.shown("mcp-server-claude-self")), "server not listed")
        self.click("mcp-check")
        self.assertTrue(wait_for(lambda: self.mcp.servers and self.mcp.servers[0]["health"].get("ok"), timeout=30),
                        "health check did not pass")
        self.find("mcp-profile-name").setProperty("text", "dbonly")
        self.find("mcp-member-db").setProperty("checked", True)
        spin()
        self.click("mcp-profile-save")
        self.assertEqual(registry.profiles(), {"dbonly": ["db"]})
        self.assertEqual(self.warnings, [])

    def test_permissions_page_shows_recorded_requests(self):
        from omaorchestra import permissions
        permissions.log({"kind": "asked", "session": "s1", "project": "/w/app", "message": "Allow Bash?"})
        permissions.log({"kind": "answered", "session": "s1", "outcome": "continued"})
        self.click("nav-permissions")
        self.assertTrue(wait_for(lambda: self.shown("approval-0")), "request not shown")
        self.assertEqual(self.warnings, [])

    def test_reconnects_after_the_daemon_restarts(self):
        self.add_session("w1", "idle", "/tmp/a")
        self.assertTrue(wait_for(lambda: self.shown("row-w1")), 'self.shown("row-w1")')
        self.daemon.terminate()
        self.daemon.wait(timeout=5)
        self.assertTrue(wait_for(lambda: not self.sessions.connected), "did not notice the daemon going")
        env = self.daemon_env()
        self.daemon = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env, stderr=self.daemon_log)
        self.assertTrue(wait_for(lambda: self.sessions.connected, timeout=10), "did not reconnect")
        self.assertTrue(wait_for(lambda: self.shown("row-w1")), "sessions not shown after reconnecting")
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
