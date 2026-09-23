"""Qt objects the QML talks to: the theme and the live session list."""

import subprocess
import threading
import time

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .. import changes, client, control, transcript, windows
from . import present
from . import theme as theme_file

RECONNECT_SECONDS = 2


def monospace_family():
    """The family fontconfig's `monospace` resolves to, as the Omarchy shell uses."""
    try:
        out = subprocess.run(["fc-match", "-f", "%{family[0]}", "monospace"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or "monospace"


class Theme(QObject):
    """Omarchy theme colours, reloaded when the theme changes."""

    changed = Signal()

    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self.path = path or theme_file.colors_path()
        self.colors = theme_file.load(self.path)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.reload)
        self.watcher.directoryChanged.connect(self.reload)
        self._watch()

    def _watch(self):
        # A theme switch may replace the file or the whole directory, which
        # drops it from the watcher, so re-add whatever exists each time.
        for p in (self.path, self.path.parent, self.path.parent.parent):
            if p.exists() and str(p) not in self.watcher.files() + self.watcher.directories():
                self.watcher.addPath(str(p))

    @Slot()
    def reload(self, *_):
        colors = theme_file.load(self.path)
        self._watch()
        if colors != self.colors:
            self.colors = colors
            self.changed.emit()

    def _color(role, notify=changed):  # a default, since class scope is not visible inside
        return Property(str, lambda self: self.colors[role], notify=notify)

    background = _color("background")
    surface = _color("surface")
    foreground = _color("foreground")
    accent = _color("accent")
    muted = _color("muted")
    selection = _color("selection")
    urgent = _color("urgent")
    dark = Property(bool, lambda self: self.colors["mode"] != "light", notify=changed)
    del _color


class Sessions(QObject):
    """Sessions from omaorchestrad, kept current over a subscription.

    A background thread holds the subscription and reconnects when the daemon
    goes away; its signals are delivered on the Qt thread.
    """

    changed = Signal()
    changesReady = Signal(str, "QVariantMap")  # session id, changes.uncommitted() result
    _snapshot = Signal(list)
    _event = Signal(dict)
    _lost = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.by_id = {}
        self._connected = False
        self._snapshot.connect(self._on_snapshot)
        self._event.connect(self._on_event)
        self._lost.connect(self._on_lost)
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._listen, name="omaorchestra-subscribe", daemon=True)
        self.thread.start()

    def _listen(self):
        while True:
            try:
                stream = client.subscribe()
                self._snapshot.emit(next(stream))
                for message in stream:
                    self._event.emit(message)
            except (client.DaemonUnavailable, StopIteration, OSError, ValueError):
                pass
            self._lost.emit()
            time.sleep(RECONNECT_SECONDS)

    @Slot(list)
    def _on_snapshot(self, sessions):
        self.by_id = {s["id"]: s for s in sessions}
        self._connected = True
        self.changed.emit()

    @Slot(dict)
    def _on_event(self, message):
        if message.get("event") == "session":
            self.by_id[message["session"]["id"]] = message["session"]
        elif message.get("event") == "removed":
            self.by_id.pop(message.get("id"), None)
        self.changed.emit()

    @Slot()
    def _on_lost(self):
        if self._connected:
            self._connected = False
            self.changed.emit()

    def _count(status=None, notify=changed):  # a default, since class scope is not visible inside
        def get(self):
            return sum(1 for s in self.by_id.values() if status is None or s.get("status") == status)
        return Property(int, get, notify=notify)

    connected = Property(bool, lambda self: self._connected, notify=changed)
    rows = Property("QVariantList", lambda self: present.ordered(self.by_id.values()), notify=changed)

    @Slot(str, str, result="QVariantList")
    def filtered(self, status, text):
        return [r for r in present.ordered(self.by_id.values()) if present.matches(r, status, text)]

    @Slot(float, float, result=str)
    def duration(self, since, now):
        return present.duration(now - since) if since else ""

    @Slot(str)
    def focus(self, session_id):
        session = self.by_id.get(session_id)
        if session:
            # hyprctl calls block briefly; keep them off the UI thread.
            threading.Thread(target=self._focus, args=(dict(session),), daemon=True).start()

    @staticmethod
    def _focus(session):
        try:
            windows.focus_session(session)
        except windows.WindowError:
            pass

    @Slot(str, result="QVariantMap")
    def row(self, session_id):
        session = self.by_id.get(session_id)
        return present.row(session) if session else {}

    @Slot(str, result="QVariantList")
    def activity(self, session_id):
        session = self.by_id.get(session_id) or {}
        return transcript.activity(session.get("transcript_path")) if session.get("transcript_path") else []

    @Slot(str, result="QVariantList")
    def timeline(self, session_id):
        return present.timeline(self.by_id.get(session_id) or {})

    @Slot(str)
    def requestChanges(self, session_id):
        cwd = (self.by_id.get(session_id) or {}).get("cwd")

        def work():
            self.changesReady.emit(session_id, changes.uncommitted(cwd))

        threading.Thread(target=work, daemon=True).start()

    @Slot(str, result=str)
    def stop(self, session_id):
        """Stop the agent; returns an error message, or "" on success."""
        session = self.by_id.get(session_id)
        if not session:
            return "that session is gone"
        try:
            control.stop(session)
        except control.ControlError as e:
            return str(e)
        return ""

    @Slot(float, result=str)
    def clock(self, timestamp):
        return present.clock(timestamp)

    @Slot(str, result=str)
    def isoClock(self, iso):
        return present.iso_clock(iso)

    @Slot(str)
    def dismiss(self, session_id):
        try:
            client.request({"cmd": "remove", "session_id": session_id, "reason": "dismissed"})
        except client.DaemonUnavailable:
            pass

    @Slot(str)
    def copyPath(self, path):
        QGuiApplication.clipboard().setText(path)

    @Slot(str)
    def openFolder(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    total = _count()
    waiting = _count("needs-input")
    working = _count("working")
    idle = _count("idle")
    del _count
