"""Qt objects the QML talks to: the theme and the live session list."""

import os
import subprocess
import threading
import time

from PySide6.QtCore import (Property, QAbstractListModel, QFileSystemWatcher, QModelIndex, QObject, Qt, QTimer, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .. import (adapters, catalog, changes, client, config, control, fleet, fleet_graph, history, keys, launch,
               modeldefaults, providers, recent, roles, transcript, windows, worktrees)
from . import present, present_fleet
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
        self.refreshDone.connect(self.reload)

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


class Mcp(QObject):
    """MCP servers, their health, managed servers and profiles, and the
    permissions view, for the MCP and Permissions pages."""

    changed = Signal()
    checked = Signal()
    _ready = Signal("QVariantMap")

    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self.sessions = sessions
        self._data = {"servers": [], "managed": {}, "profiles": {}, "permissions": {}, "error": ""}
        self._checking = False
        self._ready.connect(self._set)
        self.checked.connect(self.reload)  # show the new results as soon as a check ends

    def _projects(self):
        return sorted(set(recent.load()) | {s.get("cwd") for s in self.sessions.by_id.values() if s.get("cwd")})

    @Slot()
    def reload(self):
        from .. import permissions
        from ..mcp import health, inventory, registry
        error = ""
        try:
            managed, profile_map = registry.load(), registry.profiles()
        except registry.McpError as e:
            managed, profile_map, error = {}, {}, str(e)
        results = health.results()
        servers = []
        for s in inventory.everything(self._projects()):
            r = results.get(inventory.key(s))
            servers.append({**s, "key": inventory.key(s), "health": r or {},
                            "healthText": "" if not r else (f"{len(r['tools'])} tools" if r["ok"] else r["error"])})
        self._set({"servers": servers, "managed": managed, "profiles": profile_map,
                   "permissions": permissions.everything(self._projects()), "error": error})

    @Slot("QVariantMap")
    def _set(self, data):
        self._data, self._checking = dict(data), False
        self.changed.emit()

    @Slot()
    def checkAll(self):
        from ..mcp import health, inventory
        self._checking = True
        self.changed.emit()
        entries = inventory.everything(self._projects())

        def work():
            health.check_all(entries)
            self.checked.emit()
        threading.Thread(target=work, daemon=True).start()

    def _act(self, action):
        from ..mcp import registry
        try:
            action()
        except (registry.McpError, keys.KeyError_) as e:
            return str(e)
        finally:
            self.reload()
        return ""

    @Slot(str, bool, result=str)
    def setEnabled(self, name, enabled):
        from ..mcp import registry
        return self._act(lambda: registry.set_enabled(name, enabled))

    @Slot(str, result=str)
    def removeServer(self, name):
        from ..mcp import registry
        return self._act(lambda: registry.remove(name))

    @Slot(str, "QVariantList", result=str)
    def setProfile(self, name, members):
        from ..mcp import registry
        return self._act(lambda: registry.set_profile(name, [str(m) for m in members]))

    @Slot(str, result=str)
    def removeProfile(self, name):
        from ..mcp import registry
        return self._act(lambda: registry.remove_profile(name))

    @Slot(result="QVariantList")
    def profileChoices(self):
        from ..mcp import registry
        try:
            names = list(registry.profiles())
        except registry.McpError:
            names = []
        return ([{"value": "", "label": "The agent's own servers"}] +
                [{"value": n, "label": n} for n in names] +
                [{"value": registry.NONE_PROFILE, "label": "None"}])

    servers = Property("QVariantList", lambda self: self._data["servers"], notify=changed)
    managed = Property("QVariantMap", lambda self: self._data["managed"], notify=changed)
    profiles = Property("QVariantMap", lambda self: self._data["profiles"], notify=changed)
    permissions = Property("QVariantMap", lambda self: self._data["permissions"], notify=changed)
    error = Property(str, lambda self: self._data["error"], notify=changed)
    checking = Property(bool, lambda self: self._checking, notify=changed)


class Spend(QObject):
    """The spending report (spend.report), refreshed on a background thread
    since it reads transcripts and may ask providers for balances."""

    changed = Signal()
    _ready = Signal("QVariantMap")

    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self.sessions = sessions
        self._report = {}
        self._loading = False
        self._ready.connect(self._set)

    @Slot("QVariantMap")
    def _set(self, report):
        self._report, self._loading = dict(report), False
        self.changed.emit()

    @Slot()
    def refresh(self):
        from .. import spend
        self._loading = True
        self.changed.emit()
        rows = list(self.sessions.by_id.values())
        threading.Thread(target=lambda: self._ready.emit(spend.report(rows)), daemon=True).start()

    @Slot(str, result=str)
    def resetText(self, iso):
        from .. import spend
        return spend.reset_text(iso)

    report = Property("QVariantMap", lambda self: self._report, notify=changed)
    loading = Property(bool, lambda self: self._loading, notify=changed)


class History(QObject):
    """Ended sessions (history.jsonl), for the History page and the Usage
    page's stats. Re-read when the page shows and after a session ends."""

    changed = Signal()
    changesReady = Signal(str, "QVariantMap")  # record key, history.changes() result
    _changes = Signal(str, "QVariantMap")
    OUTCOME_LABEL = {"finished": "Finished", "stopped": "Stopped", "crashed": "Crashed",
                     "never-started": "Never started", "dismissed": "Dismissed"}

    def __init__(self, sessions=None, parent=None):
        super().__init__(parent)
        self._records = []
        self._outcome = ""
        self._search = ""
        self._changes.connect(self.changesReady)
        if sessions is not None:
            # A session that ends is written to the history just after; catch it.
            sessions.ended.connect(lambda: QTimer.singleShot(1500, self.reload))
        self.reload()

    @staticmethod
    def key(record):
        return f"{record['id']}@{record.get('ended') or 0}"

    def _row(self, r):
        ended = r.get("ended") or 0
        commits = (r.get("git") or {}).get("commits") or 0
        cost = r.get("cost")
        return {**r, "key": self.key(r), "day": time.strftime("%A %-d %B %Y", time.localtime(ended)),
                "time": time.strftime("%H:%M", time.localtime(ended)),
                "length": present.duration(history.length(r)), "working": present.duration(history.worked(r)),
                "outcomeLabel": self.OUTCOME_LABEL.get(r.get("outcome"), r.get("outcome") or ""),
                "costText": ("" if not cost else ("$" if cost.get("real") else "~$") + f"{cost['usd']:.2f}"),
                "commitsText": f"{commits} commit" + ("" if commits == 1 else "s") if commits else "",
                "place": present.place(r.get("cwd")),
                "modelName": " via ".join(x for x in (history.model_label(r.get("model")), r.get("provider")) if x),
                "text": r.get("title") or r.get("task") or ""}

    @Slot()
    def reload(self):
        self._records = list(reversed(history.load()))  # newest first
        self.changed.emit()

    @Slot(str, str)
    def setFilter(self, outcome, search):
        self._outcome, self._search = outcome, search
        self.changed.emit()

    def _rows(self):
        return [self._row(r) for r in self._records
                if history.matches(r, outcome=self._outcome or None, search=self._search or None)]

    def _counts(self):
        counts = {"": len(self._records)}
        for r in self._records:
            counts[r.get("outcome")] = counts.get(r.get("outcome"), 0) + 1
        return counts

    @Slot(str, result="QVariantMap")
    def record(self, key):
        found = next((r for r in self._records if self.key(r) == key), None)
        return self._row(found) if found else {}

    @Slot(str, result=str)
    def keyFor(self, session_id):
        """The key of a session's latest record, or ""."""
        self.reload()
        found = next((r for r in self._records if r["id"] == session_id), None)
        return self.key(found) if found else ""

    @Slot(str, result="QVariantList")
    def activity(self, key):
        path = self.record(key).get("transcript")
        return transcript.activity(path) if path and os.path.exists(path) else []

    @Slot(str, result="QVariantList")
    def timeSplit(self, key):
        seconds = self.record(key).get("seconds") or {}
        return [{"status": present.STATUS_LABEL.get(k, k), "text": present.duration(v)} for k, v in seconds.items()]

    @Slot(str)
    def requestChanges(self, key):
        record = self.record(key)

        def work():
            self._changes.emit(key, history.changes(record) if record else {"error": "that record is gone"})
        threading.Thread(target=work, daemon=True).start()

    @Slot(str, result=str)
    def resume(self, key):
        """Returns an error message, or ""."""
        record = self.record(key)
        if not record:
            return "that record is gone"
        try:
            launch.resume(record)
        except (launch.LaunchError, KeyError) as e:
            return str(e)
        return ""

    @Slot(int, result="QVariantMap")
    def stats(self, days):
        """For the Usage page: the last `days` days (0: everything)."""
        from .. import permissions
        since = time.time() - days * 86400 if days else None
        records = [r for r in self._records if history.matches(r, since=since)]
        answered = [e for e in permissions.record(limit=10_000)
                    if e.get("waited") is not None and (since is None or e["at"] >= since)]
        data = history.stats(records, answered)
        for key in ("by_project", "by_agent", "by_model"):
            for row in data[key]:
                row["workingText"] = present.duration(row["working"])
                row["costText"] = (("~$" if row["estimated"] else "$") + f"{row['usd']:.2f}") if row["usd"] else ""
                if key == "by_model":
                    row["name"] = history.model_label(row["name"]) or row["name"]
        waits = data["waits"]
        waits["medianText"] = present.duration(waits["median_seconds"]) if waits["median_seconds"] is not None else ""
        waits["longestText"] = present.duration(waits["longest_seconds"]) if waits["longest_seconds"] is not None else ""
        return data

    rows = Property("QVariantList", _rows, notify=changed)
    counts = Property("QVariantMap", _counts, notify=changed)
    total = Property(int, lambda self: len(self._records), notify=changed)


class Away(QObject):
    """Away mode (away.py), kept current from the session subscription."""

    changed = Signal()

    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self._state = {}
        sessions.awayUpdated.connect(self._update)

    @Slot("QVariantMap")
    def _update(self, state):
        self._state = dict(state)
        self.changed.emit()

    @Slot(str, result=str)
    def setMode(self, mode):
        """Returns an error message, or ""."""
        try:
            response = client.request({"cmd": "away", "mode": mode})
        except client.DaemonUnavailable:
            return "omaorchestrad is not running"
        if not response.get("ok"):
            return response.get("error") or "the daemon refused"
        self._update(response["away"])
        return ""

    def _text(self):
        state = self._state
        if not state:
            return ""
        if state.get("away"):
            why = {"locked": " (locked)", "idle": " (idle)"}.get(state.get("reason"), "")
            return f"Away{why}: " + ("pushing to your phone" if state.get("push") else "answering prompts from your phone")
        return "At the desk (until you switch)" if state.get("mode") == "off" else "At the desk"

    known = Property(bool, lambda self: bool(self._state), notify=changed)
    active = Property(bool, lambda self: bool(self._state.get("active", self._state.get("push"))), notify=changed)
    away = Property(bool, lambda self: bool(self._state.get("away")), notify=changed)
    mode = Property(str, lambda self: self._state.get("mode", ""), notify=changed)
    text = Property(str, _text, notify=changed)


class Queue(QObject):
    """The daemon's task queue, kept current from the session subscription."""

    changed = Signal()

    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self._state = {"held": False, "busy": 0, "limit": 0, "blocked": None, "tasks": []}
        self._rows = []
        sessions.queueUpdated.connect(self._update)

    @Slot("QVariantMap")
    def _update(self, snapshot):
        self._state = dict(snapshot)
        self._state["tasks"] = [{**t, "place": present.place(t.get("cwd"))} for t in snapshot.get("tasks") or []]
        self._rows = self._grouped(self._state["tasks"])
        self.changed.emit()

    @staticmethod
    def _grouped(tasks):
        """The tasks for the page, with a fleet run's nodes as one row per run
        (where its first one is). `index` is each task's place in the queue."""
        rows, runs = [], {}
        for i, t in enumerate(tasks):
            if not t.get("fleet"):
                rows.append({**t, "index": i})
                continue
            run = runs.get(t["fleet"])
            if run is None:
                run = runs[t["fleet"]] = {"fleetRow": True, "id": "fleet:" + t["fleet"], "fleet": t["fleet"], "nodes": [],
                                          "place": t.get("place"), "state": t.get("state"), "index": i}
                rows.append(run)
            run["nodes"].append(t.get("node") or "")
        return rows

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

    @Slot(str, str, str, str, bool, bool, str, str, str, result="QVariantMap")
    def add(self, task, folder, model, permission_mode, worktree, paused, provider_id, mcp_profile, agent):
        import os
        from .. import routing
        if provider_id:  # fail now, not when the task's turn comes
            try:
                routing.claude_code_env(providers.get(provider_id), model or None)
            except (providers.ProviderError, routing.RoutingError) as e:
                return {"error": str(e)}
        item = {"task": task, "cwd": os.path.expanduser(folder), "model": model or None,
                "permission_mode": permission_mode or None, "worktree": worktree, "extra": [],
                "provider": provider_id or None, "mcp_profile": mcp_profile or None, "agent": agent or "claude",
                **launch.agent_environment(agent or "claude")}
        return self._send({"cmd": "queue-add", "item": item, "paused": paused})

    @Slot(str, str, str, str, str, bool, str, str, str, result="QVariantMap")
    def addChain(self, task, then, folder, model, permission_mode, worktree, provider_id, mcp_profile, agent):
        """A task, then a follow-up in its worktree once it finishes (chain.py)."""
        first = self.add(task, folder, model, permission_mode, worktree, False, provider_id, mcp_profile, agent)
        if first.get("error"):
            return first
        import os
        item = {"task": then, "cwd": os.path.expanduser(folder), "model": model or None,
                "permission_mode": permission_mode or None, "extra": [], "provider": provider_id or None,
                "agent": agent or "claude", "after": first["item"]["id"], "same_worktree": True,
                **launch.agent_environment(agent or "claude")}
        return self._send({"cmd": "queue-add", "item": item})

    @Slot(result="QVariantList")
    def recipeChoices(self):
        from .. import recipes
        try:
            found = recipes.load()
        except recipes.RecipeError as e:
            return [{"value": "", "label": "None: one task", "description": f"recipes.toml: {e}"}]
        return [{"value": "", "label": "None: one task", "description": ""}] + [
            {"value": name, "label": name, "description": r.get("description", "")} for name, r in sorted(found.items())]

    @Slot(str, str, str, str, bool, str, result="QVariantMap")
    def runRecipe(self, name, task, folder, model, worktree, provider_id):
        import os
        from .. import recipes
        try:
            steps = recipes.items(name, task, os.path.expanduser(folder),
                                  base={"worktree": worktree, "provider": provider_id or None, "model": model or None})
        except recipes.RecipeError as e:
            return {"error": str(e)}
        after, response = None, {}
        for step in steps:
            item = {**step, **launch.agent_environment(step["agent"])}
            if after:
                item["after"] = after
            response = self._send({"cmd": "queue-add", "item": item})
            if response.get("error"):
                return response
            after = response["item"]["id"]
        return response

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
    rows = Property("QVariantList", lambda self: self._rows, notify=changed)
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
    awayUpdated = Signal("QVariantMap")  # away mode (away.Away.state()), likewise
    fleetsUpdated = Signal(list)  # current fleet runs, from the snapshot
    fleetUpdated = Signal(dict)  # one run changed (a fleet event's state)
    ended = Signal()  # a session went away (its history record follows)
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
                if "away" in snapshot:
                    self.awayUpdated.emit(snapshot["away"])
                if "fleets" in snapshot:
                    self.fleetsUpdated.emit(snapshot["fleets"])
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
        if message.get("event") == "away":
            self.awayUpdated.emit(message["away"])
            return
        if message.get("event") == "fleet":
            if message.get("state"):
                self.fleetUpdated.emit(message["state"])
            return
        if message.get("event") == "session":
            self.by_id[message["session"]["id"]] = message["session"]
        elif message.get("event") == "removed":
            self.by_id.pop(message.get("id"), None)
            self.ended.emit()
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

    @Slot(str, str, result=str)
    def handoff(self, session_id, agent):
        """Start `agent` on this session's work; returns an error, or ""."""
        import os
        try:
            response = client.request({"cmd": "handoff", "session_id": session_id, "agent": agent,
                                       "path": os.environ.get("PATH")}, timeout=30)
        except client.DaemonUnavailable:
            return "omaorchestrad is not running"
        return "" if response.get("ok") else response.get("error") or "the hand-off failed"

    @Slot(str, result=str)
    def stop(self, session_id):
        """Stop the agent; returns an error message, or "" on success."""
        session = self.by_id.get(session_id)
        if not session:
            return "that session is gone"
        control.announce(session_id, client.request)
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

    @Slot(str, str, str, result="QVariantList")
    def modelChoices(self, folder, provider_id, agent):
        """Models for the form: the subscription's (with the folder's default),
        or a routed provider's Claude models. Other agents take a typed name."""
        import os
        from .. import routing
        if agent and agent != "claude":
            return [{"value": "", "label": f"Default ({adapters.get(agent).label}'s own)"}]
        if provider_id:
            try:
                provider = providers.get(provider_id)
            except providers.ProviderError:
                return [{"value": "", "label": "Default"}]
            models = [m for m in catalog.cached().get(provider_id, {}).get("models", [])
                      if routing.is_claude_model(provider, m["id"])]
            return [{"value": "", "label": "Agent's default"}] + [
                {"value": m["id"], "label": m["id"]} for m in models]
        default = modeldefaults.for_folder(os.path.expanduser(folder))[0] if self.folderExists(folder) else None
        return present.model_choices(catalog.all_models(), default)

    @Slot(result="QVariantList")
    def agentChoices(self):
        """Agents that are installed, Claude Code first, with what each supports."""
        import shutil
        return [{"value": a.name, "label": a.label, "routing": a.supports_routing, "mcpProfile": a.supports_mcp_profile}
                for a in adapters.ADAPTERS.values() if a.name == "claude" or shutil.which(a.binary())]

    @Slot(result="QVariantList")
    def providerChoices(self):
        """Subscription first, then the providers Claude Code can run through."""
        from .. import routing
        try:
            routed = [p for p in providers.load() if routing.routable(p)]
        except providers.ProviderError:
            routed = []
        return [{"value": "", "label": "Subscription"}] + [
            {"value": p["id"], "label": f"{p['id']} ({p['label']}, billed per token)"} for p in routed]

    @Slot(str, str)
    def rememberModel(self, folder, model):
        import os
        modeldefaults.set_for(os.path.expanduser(folder), model)
    permissionChoices = Property("QVariantList", lambda self: present.PERMISSION_CHOICES, constant=True)

    @Slot(result="QVariantList")
    def recentFolders(self):
        return [present.place(f) for f in present.recent_folders(recent.load(), self.by_id.values())]

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

    @Slot(str, str, str, str, bool, str, str, str, result="QVariantMap")
    def launch(self, task, folder, model, permission_mode, worktree, provider_id, mcp_profile, agent):
        """Start an agent on `task`; returns {"id": ...} or {"error": ...}."""
        import os
        try:
            result = launch.run(task, os.path.expanduser(folder), model=model or None,
                                permission_mode=permission_mode or None, worktree=worktree,
                                provider=provider_id or None, mcp_profile=mcp_profile or None, agent=agent or "claude")
        except launch.LaunchError as e:
            return {"error": str(e)}
        return {"id": result["id"], "tracked": result["tracked"], "note": result["note"]}

    @Slot(str, result=str)
    def costText(self, session_id):
        """ "$1.23 spent via openrouter" or "~$4.56 API-equivalent", or ""."""
        from .. import costs
        session = self.by_id.get(session_id)
        if not session or not session.get("transcript_path"):
            return ""
        c = costs.session_cost(session)
        if c["real"]:
            return f"${c['usd']:.2f} spent via {session['provider']}"
        return f"~${c['usd']:.2f} API-equivalent" if c["usd"] else ""

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


class KeyedListModel(QAbstractListModel):
    """A list for QML whose rows change in place: set_items() matches rows by
    `key`, so a row that is still there is updated (not rebuilt), and its
    delegate keeps its state (a selection, an open detail, an animation).
    Roles: `key`, `item` (the row's dict), `group` (for ListView sections)."""

    KEY, ITEM, GROUP = Qt.UserRole + 1, Qt.UserRole + 2, Qt.UserRole + 3
    countChanged = Signal()

    def __init__(self, key="id", parent=None):
        super().__init__(parent)
        self._key = key
        self._items = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self):
        return {self.KEY: b"key", self.ITEM: b"item", self.GROUP: b"group"}

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == self.KEY:
            return str(item[self._key])
        if role == self.ITEM:
            return item
        if role == self.GROUP:
            return item.get("groupLabel") or item.get("group") or ""
        return None

    def set_items(self, items):
        before = len(self._items)
        wanted = [str(i[self._key]) for i in items]
        for row in reversed(range(len(self._items))):
            if str(self._items[row][self._key]) not in wanted:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()
        for target, item in enumerate(items):
            keys = [str(i[self._key]) for i in self._items]
            key = str(item[self._key])
            if key not in keys:
                self.beginInsertRows(QModelIndex(), target, target)
                self._items.insert(target, item)
                self.endInsertRows()
                continue
            now = keys.index(key)
            if now != target:  # rows before `target` are already in place, so it moves up
                self.beginMoveRows(QModelIndex(), now, now, QModelIndex(), target)
                self._items.insert(target, self._items.pop(now))
                self.endMoveRows()
            if self._items[target] != item:
                self._items[target] = item
                self.dataChanged.emit(self.index(target), self.index(target), [self.ITEM, self.GROUP])
        if len(self._items) != before:
            self.countChanged.emit()

    def items(self):
        return list(self._items)

    count = Property(int, lambda self: len(self._items), notify=countChanged)

    @Slot(int, result="QVariantMap")
    def get(self, row):
        return self._items[row] if 0 <= row < len(self._items) else {}


class Fleets(QObject):
    """Fleet runs for the omafleet tab, kept current from the subscription
    (the snapshot's current runs, then each run as it changes). What the tab
    draws comes from present_fleet; what it asks goes to the daemon."""

    changed = Signal()  # the run list or its counts
    runChanged = Signal(str)  # one run's state (its id)

    def __init__(self, sessions, parent=None):
        super().__init__(parent)
        self.sessions = sessions
        self.by_id = {}
        self._revision = 0  # goes up on every change: QML bindings read it to re-run
        self._runs = KeyedListModel("id", self)
        self._boards, self._graphs = {}, {}  # run id -> KeyedListModel
        sessions.fleetsUpdated.connect(self._set_all)
        sessions.fleetUpdated.connect(self._set_one)

    # ------------------------------------------------------------ data

    @Slot(list)
    def _set_all(self, states):
        self.by_id = {s["id"]: s for s in states}
        self._publish()
        for run_id in self.by_id:
            self.runChanged.emit(run_id)

    @Slot(dict)
    def _set_one(self, state):
        # An answer's reply and the subscription's events can arrive out of
        # order: never let an older state replace a newer one.
        known = self.by_id.get(state["id"])
        if known and known.get("updated", 0) > state.get("updated", 0):
            return
        self.by_id[state["id"]] = state
        self._publish()
        self.runChanged.emit(state["id"])

    def _publish(self):
        self._runs.set_items(present_fleet.rows(fleet.current(list(self.by_id.values()))))
        for run_id, model in self._boards.items():
            if run_id in self.by_id:
                model.set_items(fleet.board(self.by_id[run_id]))
        for run_id, model in self._graphs.items():
            if run_id in self.by_id:
                model.set_items(present_fleet.graph(self.by_id[run_id])["nodes"])
        self._revision += 1
        self.changed.emit()

    @Slot()
    def refresh(self):
        response = self._send({"cmd": "fleet-list"})
        if "runs" in response:
            self._set_all(fleet.current(response["runs"]))

    runs = Property(QObject, lambda self: self._runs, constant=True)
    revision = Property(int, lambda self: self._revision, notify=changed)
    needing = Property(int, lambda self: sum(1 for s in self.by_id.values()
                                             if present_fleet.group(s) == "needs-you"), notify=changed)
    count = Property(int, lambda self: len(self.by_id), notify=changed)

    @Slot(str, result="QVariantMap")
    def row(self, run_id):
        state = self.by_id.get(run_id)
        return present_fleet.row(state) if state else {}

    @Slot(str, result="QVariantMap")
    def state(self, run_id):
        return self.by_id.get(run_id) or {}

    @Slot(str, result="QVariantList")
    def cards(self, run_id):
        state = self.by_id.get(run_id)
        return present_fleet.cards(state) if state else []

    @Slot(str, result=QObject)
    def board(self, run_id):
        """The run's board rows, as a model that updates in place."""
        if run_id not in self._boards:
            self._boards[run_id] = KeyedListModel("slice", self)
        if run_id in self.by_id:
            self._boards[run_id].set_items(fleet.board(self.by_id[run_id]))
        return self._boards[run_id]

    @Slot(str, result=QObject)
    def graphNodes(self, run_id):
        if run_id not in self._graphs:
            self._graphs[run_id] = KeyedListModel("key", self)
        if run_id in self.by_id:
            self._graphs[run_id].set_items(present_fleet.graph(self.by_id[run_id])["nodes"])
        return self._graphs[run_id]

    @Slot(str, result="QVariantMap")
    def graph(self, run_id):
        state = self.by_id.get(run_id)
        return present_fleet.graph(state) if state else {"nodes": [], "edges": [], "columns": 0, "lanes": 0}

    @Slot(str, result="QVariantMap")
    def timeline(self, run_id):
        response = self._send({"cmd": "fleet-show", "run": run_id}, update=False)
        if "run" not in response:
            return {"lanes": [], "gates": [], "length": ""}
        return present_fleet.timeline(response["run"], response.get("activity") or [])

    @Slot(str, str, result="QVariantMap")
    def node(self, run_id, node_id):
        state = self.by_id.get(run_id)
        if not state or node_id not in state["nodes"]:
            return {}
        detail = present_fleet.node_detail(state, node_id)
        detail["live"] = detail["session"] in self.sessions.by_id
        return detail

    @Slot(str, str, result=str)
    def lastReply(self, run_id, node_id):
        """A node's last reply, as its agent recorded it (for a reply that
        could not be read)."""
        n = (self.by_id.get(run_id) or {}).get("nodes", {}).get(node_id) or {}
        session = self.sessions.by_id.get(n.get("session")) or {}
        return fleet.read_reply(session) if session else ""

    @Slot(str, result=str)
    def stopRuleHint(self, goal):
        return present_fleet.stop_rule_hint(goal)

    # ------------------------------------------------------------ actions

    def _send(self, payload, timeout=30, update=True):
        """A daemon request; a run in the answer replaces ours (not for a
        read, which must not look like a change: the timeline re-reads on
        every change)."""
        try:
            response = client.request(payload, timeout=timeout)
        except client.DaemonUnavailable:
            return {"error": "omaorchestrad is not running"}
        if not response.get("ok"):
            return {"error": response.get("error") or "the daemon refused"}
        if update and response.get("run"):
            # Just after the caller returns: a card whose button asked for
            # this goes away with the change, and must finish its handler first.
            run = response["run"]
            QTimer.singleShot(0, lambda: self._set_one(run))
        return response

    def _act(self, payload, done):
        response = self._send(payload)
        if "error" in response:
            return {"error": response["error"]}
        return {"message": done, "id": (response.get("run") or {}).get("id", "")}

    @Slot(str, str, str, float, str, result="QVariantMap")
    def start(self, goal, folder, fleet_name, budget, shape):
        """Start a run; `budget` below 0 keeps the fleet's."""
        response = self._send({"cmd": "fleet-start", "goal": goal, "folder": os.path.expanduser(folder),
                               "fleet": fleet_name or "auto", "shape": shape or None,
                               "budget": budget if budget >= 0 else None, "path": os.environ.get("PATH")})
        if "error" in response:
            return {"error": response["error"]}
        return {"id": response["run"]["id"]}

    @Slot(str, str, str, result="QVariantMap")
    def approve(self, run_id, gate, note):
        return self._act({"cmd": "fleet-approve", "run": run_id, "gate": gate or None, "note": note or None},
                         "approved")

    @Slot(str, str, result="QVariantMap")
    def sendBack(self, run_id, note):
        return self._act({"cmd": "fleet-send-back", "run": run_id, "note": note}, "sent back to the architect")

    @Slot(str, str, result="QVariantMap")
    def drop(self, run_id, slice_id):
        return self._act({"cmd": "fleet-drop", "run": run_id, "slice": slice_id}, f"dropped {slice_id}")

    @Slot(str, str, result="QVariantMap")
    def shape(self, run_id, shape):
        return self._act({"cmd": "fleet-shape", "run": run_id, "shape": shape}, f"shape: {shape}")

    @Slot(str, str, str, result="QVariantMap")
    def acceptFiles(self, run_id, node_id, reason):
        return self._act({"cmd": "fleet-accept-scope", "run": run_id, "node": node_id, "reason": reason},
                         "accepted; on to review")

    @Slot(str, str, result="QVariantMap")
    def undoFiles(self, run_id, node_id):
        return self._act({"cmd": "fleet-scope-back", "run": run_id, "node": node_id},
                         "sent to a new builder to undo them")

    @Slot(str, str, result="QVariantMap")
    def retry(self, run_id, note):
        return self._act({"cmd": "fleet-retry", "run": run_id, "note": note or None}, "trying again")

    @Slot(str, float, int, result="QVariantMap")
    def limits(self, run_id, budget, steps):
        return self._act({"cmd": "fleet-limits", "run": run_id, "budget": budget, "max_steps": steps},
                         "limits changed")

    @Slot(str, bool, result="QVariantMap")
    def pause(self, run_id, paused):
        return self._act({"cmd": "fleet-pause" if paused else "fleet-resume", "run": run_id},
                         "paused: nothing new starts" if paused else "resumed")

    @Slot(str, result="QVariantMap")
    def cancel(self, run_id):
        return self._act({"cmd": "fleet-cancel", "run": run_id}, "cancelled: nothing more starts")

    @Slot(str, result="QVariantMap")
    def closeCheck(self, run_id):
        response = self._send({"cmd": "fleet-close", "run": run_id, "check": True}, update=False)
        if "error" in response:
            return {"error": response["error"]}
        checks = [{"ok": ok, "text": what} for ok, what in response["checks"]]
        return {"checks": checks, "ready": all(c["ok"] for c in checks)}

    @Slot(str, result="QVariantMap")
    def close(self, run_id):
        response = self._send({"cmd": "fleet-close", "run": run_id})
        if "error" in response:
            return {"error": response["error"]}
        return {"message": "closed", "notes": response.get("notes") or []}

    @Slot(str)
    def takeOver(self, session_id):
        """Work in a node's session yourself: its window, brought forward."""
        self.sessions.focus(session_id)

    # ------------------------------------------------------------ roles and fleets

    @Slot(str, result="QVariantMap")
    def roles(self, folder):
        found, problems = roles.load(os.path.expanduser(folder) if folder else None)
        order = {name: i for i, name in enumerate(roles.FLEET_ROLES)}
        listed = sorted(found.values(), key=lambda r: (order.get(r.name, 99), r.name))
        return {"roles": [{**r.summary(), "prompt": r.prompt} for r in listed], "problems": problems,
                "dir": str(roles.user_dir())}

    @Slot(str, str, result="QVariantMap")
    def copyRole(self, name, folder):
        """Copy a role to yours, to change it there."""
        try:
            role = roles.get(name, os.path.expanduser(folder) if folder else None)
        except roles.RoleError as e:
            return {"error": str(e)}
        target = roles.user_dir() / f"{name}.md"
        if target.exists():
            return {"error": f"you already have {target}"}
        source = role.path if role.source != "built-in" else str(roles.BUILTIN_DIR / f"{name}.md")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(open(source).read())
        except OSError as e:
            return {"error": str(e)}
        return {"message": f"copied to {target}", "path": str(target)}

    @Slot(result="QVariantMap")
    def templates(self):
        try:
            found = fleet_graph.load()
        except fleet_graph.FleetError as e:
            return {"templates": [], "error": str(e), "path": str(fleet_graph.path())}
        return {"templates": [{**spec, "drawing": present_fleet.template_shape(spec)}
                              for _, spec in sorted(found.items())], "error": "", "path": str(fleet_graph.path())}

    @Slot(str)
    def openInEditor(self, path):
        """Open a role file, or fleets.toml (made, commented, if missing)."""
        if not path:
            target = fleet_graph.path()
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# Your fleets for omaorchestra (`omaorchestra fleet templates` lists them).\n"
                                  "# Uncomment and change this one, or add your own:\n#\n"
                                  + "".join(f"# {line}\n" for line in fleet_graph.EXAMPLE.splitlines()))
            path = str(target)
        subprocess.Popen(["omarchy-launch-editor", path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
