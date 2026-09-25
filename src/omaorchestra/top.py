"""`omaorchestra top`: sessions and the queue in a terminal, sized for a phone.

Made for a phone over SSH (Termius, Blink) as much as for a desk: about 40
columns are enough, and every action is both a key and a button to tap
(terminal mouse reporting), since phone keyboards make keys awkward. Sessions,
the queue and away mode come live from the daemon's subscription, as in the
app.

Only what makes sense from afar is here: answer a permission prompt (in
away mode, approvals.py), dismiss, stop (after a yes), hand off, and the
queue (add, pause, resume, cancel, hold, release). Focusing a
window would happen on a desk nobody is at.

The screen is drawn from plain data (`Top.render` returns text, styles and
tap targets), so it is tested without a terminal; `run` is the curses part.
"""

import os
import queue as queues
import textwrap
import threading
import time
from pathlib import Path

from . import approvals, client, config, control, launch, recent
from .app import present

AWAY_NEXT = {"auto": "on", "on": "off", "off": "auto"}
STATUS_WORD = {"needs-input": "waiting", "working": "working", "idle": "idle"}
STATUS_STYLE = {"needs-input": "urgent", "working": "work", "idle": "dim"}
TASK_WORD = {"pending": "waiting its turn", "paused": "paused", "failed": "failed"}
TASK_STYLE = {"pending": "", "paused": "dim", "failed": "urgent"}
MIN_WIDTH, MIN_HEIGHT = 30, 10
NOTE_SECONDS = 6
ROW_HEIGHT = 2  # every list row: a headline and a quieter second line (a bigger tap target)


def one_line(text):
    return " ".join(str(text or "").split())


def clip(text, width):
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


class Screen:
    """Lines of (text, style) segments, clipped to `width`, and where each
    tap target is: hits are (y, x from, x to, key)."""

    def __init__(self, width):
        self.width = width
        self.lines = []
        self.hits = []

    def line(self, *segments, right=None):
        """Add a line; `right` is a segment pinned to the right edge."""
        y, x, out = len(self.lines), 0, []
        for text, style, *key in segments:
            if x >= self.width:
                break
            text = clip(text, self.width - x)
            out.append((x, text, style))
            if key and key[0] is not None:
                self.hits.append((y, x, x + len(text), key[0]))
            x += len(text)
        if right:
            text, style, *key = right
            start = self.width - len(text)
            if start > x:
                out.append((start, text, style))
                if key and key[0] is not None:
                    self.hits.append((y, start, self.width, key[0]))
        self.lines.append(out)

    def row_hit(self, key):
        """Make the whole of the last line tap `key` (list rows)."""
        self.hits.append((len(self.lines) - 1, 0, self.width, key))

    def text(self):
        """The screen as plain text (for tests)."""
        rows = []
        for line in self.lines:
            row = ""
            for x, text, _ in line:
                row = row.ljust(x) + text
            rows.append(row)
        return "\n".join(rows)


class Top:
    def __init__(self, request=client.request, stop=control.stop, wait=control.wait_until_gone,
                 now=time.time, agents=None, background=None):
        self.request = request
        self.stop_agent, self.wait_gone = stop, wait
        self.now = now
        self.agents = agents or config.load_or_defaults()["agents"]["enabled"] or ["claude"]
        # Slow work (stopping an agent) runs here; tests run it inline.
        self.background = background or (lambda work: threading.Thread(target=work, daemon=True).start())
        self.sessions = {}
        self.queue = {"held": False, "busy": 0, "limit": 0, "blocked": None, "tasks": []}
        self.away = None
        self.approvals = []  # permission prompts waiting for a remote answer
        self.connected = False
        self.lost = False  # the daemon failed to answer (until then: still connecting)
        self.tab = "sessions"
        self.index = {"sessions": 0, "queue": 0}
        self.offset = {"sessions": 0, "queue": 0}
        self.detail = False
        self.ask = None  # a question on screen: a confirm, a choice, or the new-task form
        self.note = ("", "", 0)  # text, style, shown until
        self.hits = []
        self.running = True

    # ------------------------------------------------------------ data

    def apply(self, message):
        """A message from the subscription (the snapshot first)."""
        if "sessions" in message:
            self.sessions = {s["id"]: s for s in message["sessions"]}
            self.queue = message.get("queue") or self.queue
            self.away = message.get("away")
            self.approvals = message.get("approvals") or []
            self.connected = True
            return
        kind = message.get("event")
        if kind == "session":
            self.sessions[message["session"]["id"]] = message["session"]
        elif kind == "removed":
            self.sessions.pop(message.get("id"), None)
        elif kind == "queue":
            self.queue = message["queue"]
        elif kind == "away":
            self.away = message["away"]
        elif kind == "approvals":
            self.approvals = message["approvals"]
        elif kind == "lost":
            self.connected, self.lost = False, True

    def rows(self, tab=None):
        tab = tab or self.tab
        return present.ordered(self.sessions.values()) if tab == "sessions" else list(self.queue.get("tasks") or [])

    def selected(self):
        rows = self.rows()
        if not rows:
            return None
        self.index[self.tab] = min(self.index[self.tab], len(rows) - 1)
        return rows[self.index[self.tab]]

    def away_active(self):
        return bool(self.away and self.away.get("active", self.away.get("push")))

    def pending(self, session):
        """The oldest request of `session` waiting for a remote answer."""
        return next((a for a in self.approvals if session and a["session_id"] == session["id"]), None)

    def tell(self, text, style=""):
        self.note = (text, style, self.now() + NOTE_SECONDS)

    def send(self, payload, timeout=5):
        """A daemon request; None (and a note saying why) if it failed."""
        try:
            response = self.request(payload, timeout=timeout)
        except client.DaemonUnavailable:
            self.tell("omaorchestrad is not running", "urgent")
            return None
        if not response.get("ok"):
            self.tell(response.get("error") or "the daemon refused", "urgent")
            return None
        if "queue" in response:
            self.queue = response["queue"]
        if "away" in response:
            self.away = response["away"]
        return response

    # ------------------------------------------------------------ input

    def key(self, key):
        """One key press (a character, or up/down/left/right/enter/esc/tab/
        backspace/pgup/pgdn), or a tap target's key."""
        if self.ask:
            return self.answer(key)
        if isinstance(key, tuple):  # a tapped row, tab or choice
            what, value = key
            if what == "tab":
                self.switch(value)
            elif what == "row":
                if self.index[self.tab] == value:
                    self.detail = not self.detail
                self.index[self.tab] = value
            return
        rows = self.rows()
        if key == "q":
            self.running = False
        elif key in ("tab", "left", "right"):
            self.switch("queue" if self.tab == "sessions" else "sessions")
        elif key in ("up", "k", "down", "j", "pgup", "pgdn") and rows:
            step = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -5, "pgdn": 5}[key]
            self.index[self.tab] = max(0, min(len(rows) - 1, self.index[self.tab] + step))
        elif key == "enter" and rows:
            self.detail = not self.detail
        elif key == "esc":
            self.detail = False
        elif key == "a" and self.away:
            self.send({"cmd": "away", "mode": AWAY_NEXT[self.away["mode"]]})
        elif key == "n":
            self.new_task()
        elif self.tab == "sessions":
            self.session_key(key)
        else:
            self.queue_key(key)

    def switch(self, tab):
        self.tab, self.detail = tab, False

    def session_key(self, key):
        session = self.selected()
        if session is None or key not in ("d", "s", "h", "y", "x"):
            return
        name = session["project"]
        request = self.pending(session)
        if key in ("y", "x"):
            if not request:
                return
            if key == "x":
                self.answer_prompt(request, "deny")
                return
            self.detail = True  # the whole request on screen before saying yes
            self.ask = {"kind": "confirm", "question": "Allow this, just this once?",
                        "then": lambda: self.answer_prompt(request, "allow")}
        elif key == "d":
            if self.send({"cmd": "remove", "session_id": session["id"], "reason": "dismissed"}):
                self.tell(f"dismissed {name}")
                self.detail = False
        elif key == "s":
            self.ask = {"kind": "confirm", "question": f"Stop the agent for {name}?",
                        "then": lambda: self.stop(session)}
        elif key == "h":
            self.ask = {"kind": "choice", "question": f"Hand {name} off to", "options": list(self.agents),
                        "then": lambda agent: self.handoff(session, agent)}

    def queue_key(self, key):
        if key == "H":
            held = self.queue.get("held")
            if self.send({"cmd": "queue-release" if held else "queue-hold"}):
                self.tell("queue released" if held else "queue held: nothing new starts")
            return
        task = self.selected()
        if task is None:
            return
        title = clip(one_line(task["task"]), 30)
        if key == "p":
            resume = task["state"] in ("paused", "failed")
            if self.send({"cmd": "queue-resume" if resume else "queue-pause", "id": task["id"]}):
                self.tell(("resumed " if resume else "paused ") + title)
        elif key == "x":
            self.ask = {"kind": "confirm", "question": f"Cancel \"{title}\"?", "then": lambda: self.cancel(task)}

    def answer(self, key):
        ask = self.ask
        if key == "esc" or (ask["kind"] == "confirm" and key == "n"):
            self.ask = None
            return
        if ask["kind"] == "confirm":
            if key in ("y", "enter"):
                self.ask = None
                ask["then"]()
            return
        if ask["kind"] == "choice":
            if isinstance(key, tuple) and key[0] == "choose":
                choice = key[1]
            elif isinstance(key, str) and key.isdigit() and 1 <= int(key) <= len(ask["options"]):
                choice = int(key) - 1
            else:
                return
            self.ask = None
            ask["then"](ask["options"][choice])
            return
        # A text field of the new-task form.
        if key == "enter":
            self.form_next()
        elif key == "backspace":
            ask["value"] = ask["value"][:-1]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            ask["value"] += key

    # ------------------------------------------------------------ actions

    def stop(self, session):
        name = session["project"]
        self.tell(f"stopping {name}…")

        def work():
            control.announce(session["id"], self.request)
            try:
                self.stop_agent(session)
            except control.ControlError as e:
                self.tell(str(e), "urgent")
                return
            gone = self.wait_gone(session)
            try:
                self.request({"cmd": "list"})  # prunes it now rather than at the next check
            except client.DaemonUnavailable:
                pass
            self.tell(f"stopped {name}" if gone else f"asked {name} to stop; it has not exited yet")
        self.background(work)

    def answer_prompt(self, request, behavior):
        source = " ".join(filter(None, ["top", approvals.where_from()]))
        if self.send({"cmd": "approval-answer", "id": request["id"], "behavior": behavior, "source": source}):
            self.tell(("allowed: " if behavior == "allow" else "denied: ") + request["summary"],
                      "" if behavior == "allow" else "urgent")
            self.approvals = [a for a in self.approvals if a["id"] != request["id"]]

    def handoff(self, session, agent):
        response = self.send({"cmd": "handoff", "session_id": session["id"], "agent": agent,
                              "path": os.environ.get("PATH")}, timeout=30)
        if response:
            self.tell(f"handed {session['project']} to {agent}")

    def cancel(self, task):
        if self.send({"cmd": "queue-cancel", "id": task["id"]}):
            self.tell("cancelled " + clip(one_line(task["task"]), 30))
            self.detail = False

    def default_folder(self):
        if self.tab == "sessions" and self.selected() and self.selected().get("cwd"):
            return self.selected()["cwd"]
        folders = recent.load()
        return folders[0] if folders else str(Path.home())

    def new_task(self):
        self.ask = {"kind": "text", "field": "task", "question": "Task", "value": "", "task": None,
                    "folder": self.default_folder()}

    def form_next(self):
        ask = self.ask
        if ask["field"] == "task":
            if not ask["value"].strip():
                return
            ask.update(field="folder", question="Folder", task=ask["value"].strip(), value=present.place(ask["folder"]))
            return
        folder = Path(ask["value"].strip() or "~").expanduser()
        if not folder.is_dir():
            self.tell(f"no folder {folder}", "urgent")
            return
        task = ask["task"]
        if len(self.agents) == 1:
            self.ask = None
            self.add_task(task, folder, self.agents[0])
            return
        self.ask = {"kind": "choice", "question": "Start it with", "options": list(self.agents),
                    "then": lambda agent: self.add_task(task, folder, agent)}

    def add_task(self, task, folder, agent):
        item = {"task": task, "cwd": str(folder.resolve()), "worktree": None, "extra": [], "agent": agent,
                **launch.agent_environment(agent)}
        if self.send({"cmd": "queue-add", "item": item}, timeout=30):
            self.tell("queued " + clip(one_line(task), 30))
            self.switch("queue")
            self.index["queue"] = len(self.rows()) - 1

    # ------------------------------------------------------------ drawing

    def render(self, width, height):
        screen = Screen(width)
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            screen.line(("Window too small", "dim"))
            self.hits = []
            return screen
        self.header(screen)
        footer = self.footer(width)
        body = height - len(screen.lines) - len(footer.lines)
        if not self.connected and not self.lost:
            screen.line(("Connecting to omaorchestrad…", "dim"))
        elif not self.connected:
            for text in textwrap.wrap("omaorchestrad is not running; waiting for it to start "
                                      "(omaorchestra service install).", width)[:body]:
                screen.line((text, "dim"))
        elif self.detail and self.selected():
            self.details(screen, body)
        elif self.tab == "sessions":
            self.session_list(screen, body)
        else:
            self.queue_list(screen, body)
        while len(screen.lines) < height - len(footer.lines):
            screen.line()
        top = len(screen.lines)
        screen.lines += footer.lines
        screen.hits += [(y + top, a, b, key) for y, a, b, key in footer.hits]
        self.hits = screen.hits
        return screen

    def header(self, screen):
        waiting = sum(1 for s in self.sessions.values() if s.get("status") == "needs-input")
        count = f" {len(self.sessions)}" + (f" ({waiting}!)" if waiting else "")
        tabs = [(" Sessions" + count + " ", "tab-on" if self.tab == "sessions" else "tab", ("tab", "sessions")),
                (" ", ""),
                (f" Queue {len(self.queue.get('tasks') or [])} ", "tab-on" if self.tab == "queue" else "tab",
                 ("tab", "queue"))]
        right = None
        if self.away_active():
            right = (" away " if self.away["away"] else " here ", "urgent" if self.away["away"] else "dim", "a")
        screen.line(*tabs, right=right)
        screen.line(("─" * screen.width, "dim"))

    def visible(self, count, room):
        """The first row to show so the selection stays in view."""
        fit = max(1, room // ROW_HEIGHT)
        first = self.offset[self.tab]
        index = self.index[self.tab]
        if index < first:
            first = index
        elif index >= first + fit:
            first = index - fit + 1
        first = max(0, min(first, max(0, count - fit)))
        self.offset[self.tab] = first
        return first, fit

    def session_list(self, screen, room):
        rows = self.rows()
        if not rows:
            screen.line(("No agent sessions.", "dim"))
            screen.line(("n adds a task to the queue.", "dim"))
            return
        self.selected()
        first, fit = self.visible(len(rows), room)
        width = screen.width
        for i in range(first, min(len(rows), first + fit)):
            s = rows[i]
            chosen = i == self.index["sessions"]
            status = STATUS_WORD.get(s.get("status"), s.get("status") or "")
            age = present.duration(self.now() - s["since"])
            tail = f" {status} {age:>3} "
            screen.line((("▸ " if chosen else "  ") + clip(s["project"], width - len(tail) - 2),
                         "chosen" if chosen else "bold"),
                        right=(tail, STATUS_STYLE.get(s.get("status"), "")))
            screen.row_hit(("row", i))
            request = self.pending(s)
            if request:
                screen.line(("  ? " + clip(one_line(request["summary"]), width - 4), "urgent"))
                screen.row_hit(("row", i))
                continue
            second = s.get("message") if s.get("status") == "needs-input" else None
            second = second or s.get("title") or s.get("task") or " · ".join(
                x for x in (s.get("agent"), s.get("modelName"), s.get("branch")) if x)
            screen.line(("  " + clip(one_line(second), width - 2), "dim"))
            screen.row_hit(("row", i))

    def queue_list(self, screen, room):
        q = self.queue
        state = "held" if q.get("held") else "running"
        screen.line((f"{q.get('busy', 0)} of {q.get('limit', 0)} agents busy · queue {state}",
                     "urgent" if q.get("held") else "dim"))
        room -= 1
        if q.get("blocked"):
            screen.line((clip("waiting: " + (q["blocked"].get("text") or ""), screen.width), "work"))
            room -= 1
        rows = self.rows()
        if not rows:
            screen.line(("No queued tasks. n adds one.", "dim"))
            return
        self.selected()
        first, fit = self.visible(len(rows), room)
        for i in range(first, min(len(rows), first + fit)):
            t = rows[i]
            chosen = i == self.index["queue"]
            screen.line((("▸ " if chosen else "  ") + f"{i + 1}. " + one_line(t["task"]),
                         "chosen" if chosen else "bold"))
            screen.row_hit(("row", i))
            word = TASK_WORD.get(t["state"], t["state"])
            if t.get("error"):
                word += ": " + one_line(t["error"])
            screen.line((f"  {present.project(t.get('cwd'))} · {t.get('agent') or 'claude'} · ", "dim"),
                        (word, TASK_STYLE.get(t["state"], "dim") or "dim"))
            screen.row_hit(("row", i))

    def details(self, screen, room):
        item = self.selected()
        width = screen.width
        lines = []  # (text, style)

        def field(label, value, style=""):
            if value:
                for n, text in enumerate(textwrap.wrap(one_line(value), width - 2) or [""]):
                    lines.append(((label + ": " if n == 0 else "  ") + text if label else text, style))

        if self.tab == "sessions":
            status = STATUS_WORD.get(item.get("status"), "")
            lines.append((item["project"], "bold"))
            lines.append((f"{status} for {present.duration(self.now() - item['since'])}",
                          STATUS_STYLE.get(item.get("status"), "")))
            request = self.pending(item)
            if request:
                field("asks", request["summary"], "urgent")
                field("", "y allows just this request; x refuses it. The terminal can still answer.", "dim")
            else:
                field("", item.get("message") if item.get("status") == "needs-input" else "", "urgent")
            field("task", item.get("title") or item.get("task"))
            field("in", item.get("place"))
            field("agent", " · ".join(x for x in (item.get("agent"), item.get("modelName")) if x))
            field("branch", item.get("branch"))
            field("id", item["id"][:8])
        else:
            lines.append((TASK_WORD.get(item["state"], item["state"]), TASK_STYLE.get(item["state"]) or "dim"))
            field("", item["task"], "bold")
            field("in", present.place(item.get("cwd")))
            field("agent", " · ".join(x for x in (item.get("agent") or "claude", item.get("model")) if x))
            field("error", item.get("error"), "urgent")
            field("id", item["id"][:8])
        for text, style in lines[:room]:
            screen.line((text, style))

    def buttons(self):
        """(label, key) for what can be done now."""
        ask = self.ask
        if ask:
            if ask["kind"] == "confirm":
                return [("y Yes", "y"), ("n No", "n")]
            if ask["kind"] == "choice":
                return [(f"{i} {o}", ("choose", i - 1)) for i, o in enumerate(ask["options"], 1)] + [("esc", "esc")]
            return [("⏎ " + ("Next" if ask["field"] == "task" else "Queue it"), "enter"), ("esc Cancel", "esc")]
        items = []
        if self.selected():
            items.append(("⏎ Back" if self.detail else "⏎ More", "enter"))
        if self.tab == "sessions" and self.selected():
            if self.pending(self.selected()):
                items += [("y Allow…", "y"), ("x Deny", "x")]
            items += [("d Dismiss", "d"), ("s Stop", "s"), ("h Hand off", "h")]
        if self.tab == "queue":
            task = self.selected()
            if task:
                items.append(("p Resume" if task["state"] in ("paused", "failed") else "p Pause", "p"))
                items.append(("x Cancel", "x"))
            items.append(("H Release" if self.queue.get("held") else "H Hold", "H"))
        items.append(("n New task", "n"))
        if self.away_active():
            items.append((f"a Away: {self.away['mode']}", "a"))
        items.append(("q Quit", "q"))
        return items

    def footer(self, width):
        foot = Screen(width)
        foot.line(("─" * width, "dim"))
        ask = self.ask
        text, style, until = self.note
        noted = text and self.now() < until
        if ask and noted:  # e.g. why the form did not go on
            foot.line((text, style))
        if ask:
            if ask["kind"] == "text":
                label = ask["question"] + ": "
                room = width - len(label) - 1
                value = ask["value"] if len(ask["value"]) <= room else "…" + ask["value"][-(room - 1):]
                foot.line((label, "bold"), (value, ""), ("▏", "work"))
            else:
                foot.line((ask["question"] + ("" if ask["kind"] == "confirm" else ":"), "bold"))
        else:
            foot.line((text, style) if noted else ("", ""))
        # Buttons flow onto as many lines as they need.
        line, x = [], 0
        for label, key in self.buttons():
            label = f" {label} "
            if line and x + len(label) > width:
                foot.line(*line)
                line, x = [], 0
            line += [(label, "button", key), (" ", "")]
            x += len(label) + 1
        if line:
            foot.line(*line)
        return foot

    def click(self, y, x):
        """A tap at (y, x): the key of what is there, or None."""
        for hy, a, b, key in reversed(self.hits):
            if hy == y and a <= x < b:
                return key
        return None


# ---------------------------------------------------------------- curses

def feed(out):
    """Subscription messages into `out`, reconnecting whenever the daemon goes."""
    while True:
        try:
            for message in client.subscribe():
                out.put(message)
        except (client.DaemonUnavailable, OSError, ValueError):
            pass
        out.put({"event": "lost"})
        time.sleep(2)


def run():
    import curses
    os.environ.setdefault("ESCDELAY", "25")  # Esc responds at once rather than after a second
    return curses.wrapper(lambda screen: _main(curses, screen))


def _styles(curses):
    styles = {"": curses.A_NORMAL, "bold": curses.A_BOLD, "dim": curses.A_DIM, "chosen": curses.A_REVERSE,
              "tab": curses.A_NORMAL, "tab-on": curses.A_REVERSE | curses.A_BOLD, "button": curses.A_REVERSE,
              "urgent": curses.A_BOLD, "work": curses.A_NORMAL}
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        curses.init_pair(1, curses.COLOR_RED, background)
        curses.init_pair(2, curses.COLOR_YELLOW, background)
        styles["urgent"] = curses.color_pair(1) | curses.A_BOLD
        styles["work"] = curses.color_pair(2)
    return styles


KEYS = {"\n": "enter", "\r": "enter", "\x1b": "esc", "\t": "tab", "\x7f": "backspace", "\b": "backspace"}


def _main(curses, window):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    styles = _styles(curses)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)  # report presses at once, so taps feel immediate
    window.timeout(250)
    named = {curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
             curses.KEY_ENTER: "enter", curses.KEY_BACKSPACE: "backspace", curses.KEY_PPAGE: "pgup",
             curses.KEY_NPAGE: "pgdn"}
    wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
    wheel_down = getattr(curses, "BUTTON5_PRESSED", 0)
    tap = curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED

    top = Top()
    messages = queues.Queue()
    threading.Thread(target=feed, args=(messages,), name="omaorchestra-top", daemon=True).start()
    while top.running:
        while True:
            try:
                top.apply(messages.get_nowait())
            except queues.Empty:
                break
        height, width = window.getmaxyx()
        screen = top.render(width, height)
        window.erase()
        for y, line in enumerate(screen.lines[:height]):
            for x, text, style in line:
                try:
                    window.addnstr(y, x, text, max(0, width - x), styles.get(style, 0))
                except curses.error:
                    pass  # the bottom-right cell cannot be written; nothing is lost
        window.refresh()
        try:
            ch = window.get_wch()
        except curses.error:
            continue  # no key within the timeout: redraw (times move on)
        except KeyboardInterrupt:
            break
        if ch == curses.KEY_RESIZE:
            continue
        if ch == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
            except curses.error:
                continue
            if state & wheel_up:
                top.key("up")
            elif state & wheel_down:
                top.key("down")
            elif state & tap:
                key = top.click(y, x)
                if key is not None:
                    top.key(key)
            continue
        key = named.get(ch) if isinstance(ch, int) else KEYS.get(ch, ch)
        if key is not None:
            top.key(key)
    return 0
