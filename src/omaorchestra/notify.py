"""Desktop notifications for session changes.

Notifications go through notify-send at normal or low urgency, so the
Omarchy shell's do-not-disturb holds them back like any other app's. A
waiting notification closes itself once the session stops waiting.
"""

import asyncio
import logging
import os
import time

from .log import event


def decide(previous, session, settings, now=None):
    """What a status change calls for: "waiting", "finished", "clear" or None.

    `previous` is the session as it was before the change (None if new);
    `session` is None when the session ended.
    """
    now = time.time() if now is None else now
    before = previous.get("status") if previous else None
    after = session.get("status") if session else None
    if after == "needs-input" and before != "needs-input":
        return "waiting" if settings["waiting"] else None
    if before == "needs-input" and after != "needs-input":
        return "clear"
    if before == "working" and after == "idle":
        threshold = settings["finished_after"]
        worked = now - previous.get("status_since", now)
        if threshold and worked >= threshold:
            return "finished"
    return None


def project(session):
    cwd = str(session.get("cwd") or "").rstrip("/")
    return os.path.basename(cwd) or cwd or "An agent"


def duration(seconds):
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m" if minutes else f"{int(seconds)}s"


def content(kind, previous, session, now=None):
    """(summary, body, urgency) for a notification."""
    now = time.time() if now is None else now
    if kind == "waiting":
        return f"{project(session)} needs you", session.get("message") or "Waiting for your input", "normal"
    worked = now - previous.get("status_since", now)
    return f"{project(session)} finished", f"Worked for {duration(worked)}", "low"


class Notifier:
    """Sends, replaces and closes one notification per session.

    `spawn` and `close` are injectable for tests; `focus` is called with a
    session id when the user clicks Focus.
    """

    def __init__(self, settings, focus, spawn=asyncio.create_subprocess_exec, close=None):
        self.settings = settings
        self.focus = focus
        self.spawn = spawn
        self.close_notification = close or self._gdbus_close
        self.shown = {}  # session id -> (notification id, kind)
        self.pending = {}  # session id -> kind, while notify-send has not reported the id yet
        self.cancelled = set()  # pending waiting notifications that went stale before appearing
        self.tasks = set()
        self.warned = False

    def changed(self, previous, session):
        """Called by the daemon after every update or removal."""
        kind = decide(previous, session, self.settings)
        sid = (session or previous or {}).get("id")
        if kind in ("waiting", "finished"):
            # Marked before the task runs, so a change in the same instant
            # already sees the notification as on its way.
            self.pending[sid] = kind
            self.cancelled.discard(sid)
            self._start(self.send(kind, previous, session))
        elif kind == "clear" or session is None:
            # Only a waiting notification goes stale; a finished one stays
            # until read, even if the session has ended since.
            if self.shown.get(sid, (None, None))[1] == "waiting":
                self._start(self.clear(sid))
            elif self.pending.get(sid) == "waiting":
                self.cancelled.add(sid)

    def _start(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def send(self, kind, previous, session):
        summary, body, urgency = content(kind, previous, session)
        sid = session["id"]
        argv = ["notify-send", "--app-name=omaorchestra", f"--urgency={urgency}", "--print-id", "--wait",
                "--action=focus=Focus"]
        if sid in self.shown:
            argv.append(f"--replace-id={self.shown[sid][0]}")
        argv += [summary, body]
        try:
            proc = await self.spawn(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            self.pending.pop(sid, None)
            if not self.warned:
                event(logging.WARNING, "notifications off", error="notify-send not found")
                self.warned = True
            return
        event(logging.INFO, "notified", id=sid, kind=kind)
        first = await proc.stdout.readline()
        self.pending.pop(sid, None)
        if first.strip().isdigit():
            if sid in self.cancelled:
                # Answered before the notification even appeared.
                self.cancelled.discard(sid)
                await self.close_notification(int(first))
            else:
                self.shown[sid] = (int(first), kind)
        async for line in proc.stdout:
            if line.strip() == b"focus":
                event(logging.INFO, "notification clicked", id=sid)
                await self.focus(sid)
        await proc.wait()

    def offer(self, summary, body, label, on_accept):
        """A one-off notification with one action; `on_accept` (a coroutine
        function) runs if the user clicks it."""
        async def run():
            try:
                proc = await self.spawn("notify-send", "--app-name=omaorchestra", "--urgency=normal", "--wait",
                                        f"--action=accept={label}", summary, body,
                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                return
            async for line in proc.stdout:
                if line.strip() == b"accept":
                    await on_accept()
            await proc.wait()
        self._start(run())

    async def clear(self, sid):
        shown = self.shown.pop(sid, None)
        if shown is not None:
            await self.close_notification(shown[0])

    async def _gdbus_close(self, notification):
        proc = await self.spawn(
            "gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.freedesktop.Notifications.CloseNotification", str(notification),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
