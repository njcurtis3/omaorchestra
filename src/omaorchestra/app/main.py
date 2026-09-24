"""Start the app: `omaorchestra app`."""

import json
import os
import sys
from pathlib import Path

APP_ID = "omaorchestra"  # Wayland app id, so Hyprland rules can match class ^omaorchestra$


def activate_own_window():
    """Bring this app's window forward. Wayland does not let a client raise
    itself, so ask Hyprland (the same way `omaorchestra focus` does)."""
    from .. import windows
    try:
        for w in windows.clients():
            if w.get("pid") == os.getpid():
                windows.focus(w)
                return
    except windows.WindowError:
        pass


def run(check=False, session=""):
    warnings = []
    if check:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
    except ImportError:
        print("omaorchestra app needs PySide6: sudo pacman -S pyside6 "
              "(and run with the system python3, which pacman installs it for)", file=sys.stderr)
        return 1

    from .. import __version__
    from . import backend, instance

    if check:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        # A self-test fails on any QML warning, not only on a missing daemon.
        def collect(mode, context, message):
            if mode != QtMsgType.QtDebugMsg:
                warnings.append(message)

        qInstallMessageHandler(collect)

    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("omaorchestra")
    app.setApplicationDisplayName("omaorchestra")
    app.setDesktopFileName(APP_ID)
    QQuickStyle.setStyle("Basic")  # plain controls that take the theme's colours

    server = None
    if not check and instance.ask_running_instance(session=session):
        return 0

    theme = backend.Theme()
    sessions = backend.Sessions()
    settings = backend.Settings()
    worktree_list = backend.Worktrees()
    queue = backend.Queue(sessions)
    provider_list = backend.Providers()
    provider_list.refreshDone.connect(provider_list.reload)
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("theme", theme)
    ctx.setContextProperty("sessions", sessions)
    ctx.setContextProperty("settings", settings)
    ctx.setContextProperty("worktrees", worktree_list)
    ctx.setContextProperty("queue", queue)
    ctx.setContextProperty("providerList", provider_list)
    ctx.setContextProperty("appVersion", __version__)
    ctx.setContextProperty("fontFamily", backend.monospace_family())
    ctx.setContextProperty("initialSession", session)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        print("omaorchestra app: the interface failed to load", file=sys.stderr)
        return 1
    sessions.start()

    if not check:
        root = engine.rootObjects()[0]

        def activate(session_id):
            if session_id:
                root.showSession(session_id)
            activate_own_window()

        server = instance.listen(activate)  # noqa: F841 (kept alive by the local)

    if check:
        # Self-test: load the UI offscreen, wait for the daemon, report, exit.
        result = {}

        def report():
            result.update(loaded=True, connected=sessions.connected, sessions=sessions.total,
                          window=engine.rootObjects()[0].property("title"), background=theme.background,
                          warnings=warnings)
            app.quit()

        sessions.changed.connect(lambda: sessions.connected and report())
        QTimer.singleShot(5000, report)
        app.exec()
        print(json.dumps(result))
        return 0 if result.get("connected") and not warnings else 1

    return app.exec()
