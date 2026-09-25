"""Away mode: push to the phone only while you are away from the desk.

  auto  away while the session is locked, or after [remote] away_after
        minutes without keyboard or mouse input (the default)
  on    away until turned off: every push goes out
  off   at the desk: nothing is pushed

The mode is kept in away.json in the state folder, beside sessions.json; the
daemon rewrites it whenever you come or go, which is how the bar widget
(which never talks to the socket) shows it.

Locked comes from Omarchy's shell (`omarchy-shell lock isLocked`). Idle comes
from the compositor's ext-idle-notify protocol, spoken over the Wayland
socket with the standard library; it respects idle inhibitors, so a playing
video keeps you at the desk as it keeps the screen on. Either signal that
cannot be read counts as not away.
"""

import asyncio
import json
import logging
import os
import struct
import subprocess
import time

from .log import event

MODES = ("auto", "on", "off")
LOCK_TIMEOUT = 2
RETRY_SECONDS = 30  # between attempts to reach the compositor


def is_locked(run=subprocess.run):
    """True or False from Omarchy's lock screen, or None if it cannot say."""
    try:
        result = run(["omarchy-shell", "lock", "isLocked"], capture_output=True, text=True, timeout=LOCK_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    answer = result.stdout.strip()
    return {"true": True, "false": False}.get(answer) if result.returncode == 0 else None


class Away:
    """The mode and what was last seen of the desk; `away` is the verdict."""

    def __init__(self, path, minutes=10, on_change=None):
        self.path = path
        self.minutes = minutes
        self.on_change = on_change
        self.mode = "auto"
        self.push = False  # [remote] push: away mode means nothing without it
        self.locked = None  # None: not known (not watched, or cannot be read)
        self.idle = None
        self.since = time.time()
        try:
            saved = json.loads(path.read_text())
            if saved.get("mode") in MODES:
                self.mode = saved["mode"]
        except (OSError, ValueError, AttributeError):
            pass

    @property
    def reason(self):
        """Why you count as away ("on", "locked", "idle"), or None."""
        if self.mode == "on":
            return "on"
        if self.mode == "auto":
            if self.locked:
                return "locked"
            if self.idle:
                return "idle"
        return None

    @property
    def away(self):
        return self.reason is not None

    def state(self):
        return {"mode": self.mode, "away": self.away, "reason": self.reason, "since": self.since,
                "push": self.push, "after": self.minutes, "locked": self.locked, "idle": self.idle}

    def update(self, **facts):
        """Set mode, push, locked or idle; saves and reports when the mode,
        push, or whether (and why) you are away changes."""
        before = (self.mode, self.reason, self.push)
        for key, value in facts.items():
            setattr(self, key, value)
        if (self.mode, self.reason, self.push) == before:
            return False
        if self.reason != before[1]:
            self.since = time.time()
        self.save()
        event(logging.INFO, "away" if self.away else "at the desk", mode=self.mode, reason=self.reason)
        if self.on_change:
            self.on_change(self.state())
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": self.mode, "away": self.away, "reason": self.reason,
                                   "since": self.since, "push": self.push}, indent=2))
        os.replace(tmp, self.path)


class WaylandError(Exception):
    pass


def _string(text):
    data = text.encode() + b"\0"
    return struct.pack("<I", len(data)) + data + b"\0" * (-len(data) % 4)


def _read_string(body, offset):
    length, = struct.unpack_from("<I", body, offset)
    text = body[offset + 4:offset + 4 + length - 1].decode(errors="replace")
    return text, offset + 4 + length + (-length % 4)


def display_path(env=os.environ):
    display = env.get("WAYLAND_DISPLAY")
    if not display:
        return None
    if display.startswith("/"):
        return display
    runtime = env.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime, display) if runtime else None


class IdleWatch:
    """Tells `on_idle(True|False|None)` when there has been no input for
    `minutes`, and when there is again; None while the compositor cannot be
    reached. Reconnects on its own (Hyprland restarts, a late login)."""

    DISPLAY = 1  # wl_display, always object 1

    def __init__(self, minutes, on_idle, path=None):
        self.minutes = minutes
        self.on_idle = on_idle
        self.path = path
        self.task = None
        self.writer = None
        self.next_id = 2
        self.notifier = self.seat = self.notification = None
        self.failed = False  # log the first failure of a run, not every retry

    def start(self):
        self.task = asyncio.get_running_loop().create_task(self.run())

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None
        self.close()

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None

    async def run(self):
        while True:
            try:
                await self.session()
            except (OSError, EOFError, asyncio.IncompleteReadError, WaylandError, struct.error) as e:
                if not self.failed:
                    event(logging.WARNING, "idle detection unavailable", error=str(e) or type(e).__name__,
                          retry=RETRY_SECONDS)
                self.failed = True
            finally:
                self.close()
            self.on_idle(None)
            await asyncio.sleep(RETRY_SECONDS)

    def new_id(self):
        self.next_id += 1
        return self.next_id - 1

    def send(self, obj, opcode, payload=b""):
        self.writer.write(struct.pack("<II", obj, (8 + len(payload)) << 16 | opcode) + payload)

    async def receive(self, reader):
        obj, word = struct.unpack("<II", await reader.readexactly(8))
        size = word >> 16
        if size < 8:
            raise WaylandError("bad message from the compositor")
        return obj, word & 0xFFFF, await reader.readexactly(size - 8)

    async def session(self):
        path = self.path or display_path()
        if not path:
            raise WaylandError("no Wayland display (WAYLAND_DISPLAY is not set)")
        reader, self.writer = await asyncio.open_unix_connection(path)
        self.next_id = 2
        self.notifier = self.seat = self.notification = None
        registry, done = self.new_id(), self.new_id()
        self.send(self.DISPLAY, 1, struct.pack("<I", registry))  # get_registry
        self.send(self.DISPLAY, 0, struct.pack("<I", done))  # sync: its reply ends the global list
        found = {}
        while True:
            obj, opcode, body = await self.receive(reader)
            self.check_error(obj, opcode, body)
            if obj == registry and opcode == 0:  # global(name, interface, version)
                name, = struct.unpack_from("<I", body)
                interface, offset = _read_string(body, 4)
                version, = struct.unpack_from("<I", body, offset)
                if interface in ("wl_seat", "ext_idle_notifier_v1") and interface not in found:
                    found[interface] = (name, version)
            elif obj == done:
                break
        if "ext_idle_notifier_v1" not in found or "wl_seat" not in found:
            raise WaylandError("the compositor does not offer idle notifications")
        self.seat = self.bind(registry, "wl_seat", found["wl_seat"][0], 1)
        self.notifier = self.bind(registry, "ext_idle_notifier_v1", found["ext_idle_notifier_v1"][0], 1)
        self.notify_after(self.minutes)
        await self.writer.drain()
        if self.failed:
            event(logging.INFO, "idle detection working again")
        self.failed = False
        self.on_idle(False)
        while True:
            obj, opcode, body = await self.receive(reader)
            self.check_error(obj, opcode, body)
            if obj == self.notification and opcode in (0, 1):  # idled, resumed
                self.on_idle(opcode == 0)

    def bind(self, registry, interface, name, version):
        new = self.new_id()
        self.send(registry, 0, struct.pack("<I", name) + _string(interface) + struct.pack("<II", version, new))
        return new

    def notify_after(self, minutes):
        """Ask for a notification after `minutes` without input, replacing
        the one there was (a changed setting)."""
        self.minutes = minutes
        if not self.writer or not self.notifier:
            return
        if self.notification:
            self.send(self.notification, 0)  # destroy
            self.on_idle(False)  # the new one counts from now, and never says "resumed" before it idles
        self.notification = self.new_id()
        self.send(self.notifier, 1, struct.pack("<III", self.notification, int(minutes * 60_000), self.seat))

    def check_error(self, obj, opcode, body):
        if obj == self.DISPLAY and opcode == 0:  # wl_display.error(object, code, message)
            message, _ = _read_string(body, 8)
            raise WaylandError(f"the compositor refused: {message}")
