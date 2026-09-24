"""Qt objects the QML talks to: the theme and the live session list."""

import subprocess
import threading
import time

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .. import (catalog, changes, client, config, control, keys, launch, modeldefaults, providers, recent, transcript,
               windows, worktrees)
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


class Settings(QObject):
    """The config file, as the settings screen edits it."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._error = ""
        self._sections = []
        self.reload()

    @Slot()
    def reload(self):
        try:
            current = config.load()
            self._error = ""
        except config.ConfigError as e:
            # Show the defaults, and say why the file could not be used.
            current, self._error = config.defaults(), str(e)
        self._sections = config.describe(current)
        self.changed.emit()

    @Slot("QVariantMap", result=str)
    def save(self, changes):
        """Write {"section.key": value} changes and tell the daemon; returns a
        message for the user (empty when everything worked)."""
        grouped = {}
        for name, value in changes.items():
            section, _, key = name.partition(".")
            if isinstance(value, float) and value.is_integer():
                value = int(value)  # QML numbers arrive as floats
            grouped.setdefault(section, {})[key] = value
        try:
            config.save(grouped)
        except config.ConfigError as e:
            return str(e)
        finally:
            self.reload()
        try:
            response = client.request({"cmd": "reload"})
        except client.DaemonUnavailable:
            return ""  # it reads the file when it starts
        return "" if response.get("ok") else f"Saved, but the daemon kept its old settings: {response.get('error')}"

    @Slot()
    def openInEditor(self):
        path = config.path()
        if not path.exists():
            config.save({})  # an editor needs a file to open
        subprocess.Popen(["omarchy-launch-editor", str(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    sections = Property("QVariantList", lambda self: self._sections, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    path = Property(str, lambda self: str(config.path()), notify=changed)


class Providers(QObject):
    """Model providers and their model lists, for the Providers page."""

    changed = Signal()
    testDone = Signal(str, bool, str)  # provider id, ok, message
    refreshDone = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items, self._error = [], ""
        self.testDone.connect(lambda *a: None)

    @Slot()
    def reload(self):
        try:
            items = providers.load()
            self._error = ""
        except providers.ProviderError as e:
            items, self._error = [], str(e)
        cache = catalog.cached()
        self._items = [{**p, "needsKey": providers.needs_key(p),
                        "keyStored": bool(keys.lookup(p["id"])) if providers.needs_key(p) else False,
                        "modelCount": len(cache.get(p["id"], {}).get("models", [])),
                        "fetched": cache.get(p["id"], {}).get("fetched") or 0,
                        "lastError": cache.get(p["id"], {}).get("error") or ""} for p in items]
        self.changed.emit()

    @Slot(str, str, str, result="QVariantMap")
    def add(self, kind, provider_id, base_url):
        try:
            item = providers.add(kind, provider_id or None, base_url or None)
        except providers.ProviderError as e:
            return {"error": str(e)}
        self.reload()
        return {"id": item["id"]}

    @Slot(str, str, result=str)
    def setKey(self, provider_id, key):
        try:
            keys.store(provider_id, key)
        except keys.KeyError_ as e:
            return str(e)
        self.reload()
        return ""

    @Slot(str, result=str)
    def remove(self, provider_id):
        try:
            providers.remove(provider_id)
        except providers.ProviderError as e:
            return str(e)
        self.reload()
        return ""

    @Slot(str)
    def test(self, provider_id):
        def work():
            try:
                self.testDone.emit(provider_id, True, providers.test(providers.get(provider_id)))
            except (providers.ProviderError, keys.KeyError_) as e:
                self.testDone.emit(provider_id, False, str(e))
        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def refreshModels(self):
        def work():
            catalog.refresh()
            self.refreshDone.emit()
        threading.Thread(target=work, daemon=True).start()

    kinds = Property("QVariantList", lambda self: [{"value": k, "label": v["label"], "url": v["base_url"]}
                                                   for k, v in providers.KINDS.items()], constant=True)
    items = Property("QVariantList", lambda self: self._items, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)


class Queue(QObject):
    """The daemon's task queue, kept current from the session subscription."""

    changed = Signal()

    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self._state = {"held": False, "busy": 0, "limit": 0, "blocked": None, "tasks": []}
        sessions.queueUpdated.connect(self._update)

    @Slot("QVariantMap")
    def _update(self, snapshot):
        self._state = dict(snapshot)
        self.changed.emit()

    def _send(self, payload):
        try:
            response = client.request(payload, timeout=30)  # a start may create a worktree
        except client.DaemonUnavailable:
            return {"error": "omaorchestrad is not running"}
        if not response.get("ok"):
            return {"error": response.get("error") or "the daemon refused"}
        if "queue" in response:
            self._update(response["queue"])
        return response

    @Slot(str, str, str, str, bool, bool, result="QVariantMap")
    def add(self, task, folder, model, permission_mode, worktree, paused):
        import os
        item = {"task": task, "cwd": os.path.expanduser(folder), "model": model or None,
                "permission_mode": permission_mode or None, "worktree": worktree, "extra": [],
                **launch.agent_environment()}
        return self._send({"cmd": "queue-add", "item": item, "paused": paused})

    @Slot(str, result="QVariantMap")
    def cancel(self, task_id):
        return self._send({"cmd": "queue-cancel", "id": task_id})

    @Slot(str, bool, result="QVariantMap")
    def setPaused(self, task_id, paused):
        return self._send({"cmd": "queue-pause" if paused else "queue-resume", "id": task_id})

    @Slot(str, result="QVariantMap")
    def runNow(self, task_id):
        return self._send({"cmd": "queue-run", "id": task_id})

    @Slot(str, int, result="QVariantMap")
    def move(self, task_id, position):
        return self._send({"cmd": "queue-move", "id": task_id, "position": position})

    @Slot(bool, result="QVariantMap")
    def setHeld(self, held):
        return self._send({"cmd": "queue-hold" if held else "queue-release"})

    tasks = Property("QVariantList", lambda self: self._state["tasks"], notify=changed)
    held = Property(bool, lambda self: self._state["held"], notify=changed)
    busy = Property(int, lambda self: self._state["busy"], notify=changed)
    limit = Property(int, lambda self: self._state["limit"], notify=changed)
    blockedText = Property(str, lambda self: (self._state.get("blocked") or {}).get("text", ""), notify=changed)


class Worktrees(QObject):
    """Task worktrees for the Worktrees page."""

    changed = Signal()
    changesReady = Signal(str, "QVariantMap")  # worktree path, worktrees.changes() result

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []

    @Slot()
    def refresh(self):
        items = []
        for record in worktrees.records():
            try:
                info = worktrees.status(record)
            except worktrees.WorktreeError as e:
                info = {"exists": True, "commits": [], "dirty": False, "merged": False, "error": str(e)}
            items.append({**record, **info, "commitCount": len(info["commits"])})
        self._items = sorted(items, key=lambda r: -r.get("created", 0))
        self.changed.emit()

    @Slot(str)
    def requestChanges(self, path):
        record = next((r for r in worktrees.records() if r["path"] == path), None)

        def work():
            result = worktrees.changes(record) if record else {"error": "that worktree is gone"}
            self.changesReady.emit(path, result)

        threading.Thread(target=work, daemon=True).start()

    def _act(self, path, action):
        try:
            record = next(r for r in worktrees.records() if r["path"] == path)
            return {"message": action(record)}
        except StopIteration:
            return {"error": "that worktree is gone"}
        except worktrees.WorktreeError as e:
            return {"error": str(e)}
        finally:
            self.refresh()

    @Slot(str, result="QVariantMap")
    def merge(self, path):
        return self._act(path, worktrees.merge)

    @Slot(str, bool, result="QVariantMap")
    def remove(self, path, force):
        return self._act(path, lambda r: worktrees.remove(r, force=force))

    items = Property("QVariantList", lambda self: self._items, notify=changed)


class Sessions(QObject):
    """Sessions from omaorchestrad, kept current over a subscription.

    A background thread holds the subscription and reconnects when the daemon
    goes away; its signals are delivered on the Qt thread.
    """

    changed = Signal()
    changesReady = Signal(str, "QVariantMap")  # session id, changes.uncommitted() result
    queueUpdated = Signal("QVariantMap")  # taskqueue snapshot, from the same subscription
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
                snapshot = next(stream)
                self._snapshot.emit(snapshot["sessions"])
                if "queue" in snapshot:
                    self.queueUpdated.emit(snapshot["queue"])
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
        if message.get("event") == "queue":
            self.queueUpdated.emit(message["queue"])
            return
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
        session = self.by_id.get(session_id) or {}
        record = next((r for r in worktrees.records() if r["path"] == session.get("worktree")), None)

        def work():
            # A task with its own worktree: everything since it started.
            # Otherwise: the folder's uncommitted changes.
            result = worktrees.changes(record) if record else changes.uncommitted(session.get("cwd"))
            self.changesReady.emit(session_id, result)

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

        def settle(session=dict(session)):
            # Once it has exited, have the daemon prune it now (a `list`
            # prunes) instead of at its next periodic check.
            if control.wait_until_gone(session):
                try:
                    client.request({"cmd": "list"})
                except client.DaemonUnavailable:
                    pass

        threading.Thread(target=settle, daemon=True).start()
        return ""

    @Slot(str, result="QVariantList")
    def modelChoices(self, folder):
        import os
        default = modeldefaults.for_folder(os.path.expanduser(folder))[0] if self.folderExists(folder) else None
        return present.model_choices(catalog.all_models(), default)

    @Slot(str, str)
    def rememberModel(self, folder, model):
        import os
        modeldefaults.set_for(os.path.expanduser(folder), model)
    permissionChoices = Property("QVariantList", lambda self: present.PERMISSION_CHOICES, constant=True)

    @Slot(result="QVariantList")
    def recentFolders(self):
        return present.recent_folders(recent.load(), self.by_id.values())

    @Slot(str, result=bool)
    def inGitRepo(self, folder):
        import os
        from .. import worktrees
        return self.folderExists(folder) and worktrees.repo_root(os.path.expanduser(folder)) is not None

    @Slot(result=bool)
    def worktreeDefault(self):
        return bool(config.load_or_defaults()["tasks"]["isolate_with_worktrees"])

    @Slot(str, result=bool)
    def folderExists(self, folder):
        import os
        return bool(folder) and os.path.isdir(os.path.expanduser(folder))

    @Slot(str, str, str, str, bool, result="QVariantMap")
    def launch(self, task, folder, model, permission_mode, worktree):
        """Start an agent on `task`; returns {"id": ...} or {"error": ...}."""
        import os
        try:
            result = launch.run(task, os.path.expanduser(folder), model=model or None,
                                permission_mode=permission_mode or None, worktree=worktree)
        except launch.LaunchError as e:
            return {"error": str(e)}
        return {"id": result["id"], "tracked": result["tracked"], "note": result["note"]}

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
