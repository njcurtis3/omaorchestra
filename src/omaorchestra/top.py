"""`omaorchestra top`: sessions and the queue in a terminal, sized for a phone.

Made for a phone over SSH (Termius, Blink) as much as for a desk: about 40
columns are enough, and every action is both a key and a button to tap
(terminal mouse reporting), since phone keyboards make keys awkward. Sessions,
the queue and away mode come live from the daemon's subscription, as in the
app.

Only what makes sense from afar is here: answer a permission prompt (in
away mode, approvals.py), dismiss, stop (after a yes), hand off, the
queue (add, pause, resume, cancel, hold, release) and the schedules above it
(add, run now, pause, resume, remove), and fleet runs: read a
plan and answer its gate (approve, send back with a note, drop a slice,
choose the shape, cancel). Focusing a window would happen on a desk
nobody is at.

The screen is drawn from plain data (`Top.render` returns text, styles and
tap targets), so it is tested without a terminal; `run` is the curses part.
"""

import os
import queue as queues
import textwrap
import threading
import time
from pathlib import Path

from . import approvals, client, config, control, fleet, launch, recent, schedules
from .app import present

AWAY_NEXT = {"auto": "on", "on": "off", "off": "auto"}
STATUS_WORD = {"needs-input": "waiting", "working": "working", "idle": "idle"}
STATUS_STYLE = {"needs-input": "urgent", "working": "work", "idle": "dim"}
TASK_WORD = {"pending": "waiting its turn", "paused": "paused", "failed": "failed"}
TASK_STYLE = {"pending": "", "paused": "dim", "failed": "urgent"}
RUN_WORD = {"running": "running", "at-gate": "waiting for you", "held": "held", "done": "done",
            "cancelled": "cancelled"}
RUN_STYLE = {"running": "work", "at-gate": "urgent", "held": "urgent", "done": "dim", "cancelled": "dim"}
RUN_ORDER = {"at-gate": 0, "held": 0, "running": 1, "done": 2, "cancelled": 2}
TABS = ("sessions", "queue", "fleets")
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
        self.schedules = {"enabled": True, "schedules": []}
        self.away = None
        self.approvals = []  # permission prompts waiting for a remote answer
        self.fleets = {}  # run id -> its state
        self.connected = False
        self.lost = False  # the daemon failed to answer (until then: still connecting)
        self.tab = "sessions"
        self.index = {tab: 0 for tab in TABS}
        self.offset = {tab: 0 for tab in TABS}
        self.detail = False
        self.scroll = 0  # lines scrolled in a fleet run's details
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
            self.schedules = message.get("schedules") or self.schedules
            self.away = message.get("away")
            self.approvals = message.get("approvals") or []
            self.fleets = {s["id"]: s for s in message.get("fleets") or []}
            self.connected = True
            return
        kind = message.get("event")
        if kind == "session":
            self.sessions[message["session"]["id"]] = message["session"]
        elif kind == "removed":
            self.sessions.pop(message.get("id"), None)
        elif kind == "queue":
            self.queue = message["queue"]
        elif kind == "schedules":
            self.schedules = message["schedules"]
        elif kind == "away":
            self.away = message["away"]
        elif kind == "approvals":
            self.approvals = message["approvals"]
        elif kind == "fleet" and message.get("state"):
            self.fleets[message["run"]] = message["state"]
        elif kind == "lost":
            self.connected, self.lost = False, True

    def rows(self, tab=None):
        tab = tab or self.tab
        if tab == "fleets":
            return sorted(self.fleets.values(), key=lambda s: (RUN_ORDER.get(s["status"], 3), -s.get("updated", 0)))
        if tab == "sessions":
            return present.ordered(self.sessions.values())
        # The queue tab: schedules first (marked), then the queued tasks.
        return [{**s, "schedule_row": True} for s in self.schedules.get("schedules") or []] + \
            list(self.queue.get("tasks") or [])

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
        if "schedules" in response:
            self.schedules = response["schedules"]
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
                    self.detail, self.scroll = not self.detail, 0
                self.index[self.tab] = value
            return
        rows = self.rows()
        if key == "q":
            self.running = False
        elif key in ("tab", "left", "right"):
            step = -1 if key == "left" else 1
            self.switch(TABS[(TABS.index(self.tab) + step) % len(TABS)])
        elif key in ("up", "k", "down", "j", "pgup", "pgdn") and self.detail and self.tab == "fleets":
            self.scroll = max(0, self.scroll + {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -5, "pgdn": 5}[key])
        elif key in ("up", "k", "down", "j", "pgup", "pgdn") and rows:
            step = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -5, "pgdn": 5}[key]
            self.index[self.tab] = max(0, min(len(rows) - 1, self.index[self.tab] + step))
        elif key == "enter" and rows:
            self.detail, self.scroll = not self.detail, 0
        elif key == "esc":
            self.detail = False
        elif key == "a" and self.away:
            self.send({"cmd": "away", "mode": AWAY_NEXT[self.away["mode"]]})
        elif key == "n":
            self.new_task()
        elif self.tab == "sessions":
            self.session_key(key)
        elif self.tab == "fleets":
            self.fleet_key(key)
        else:
            self.queue_key(key)

    def switch(self, tab):
        self.tab, self.detail, self.scroll = tab, False, 0

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
        if key == "w":
            self.new_task(schedule=True)
            return
        task = self.selected()
        if task is None:
            return
        if task.get("schedule_row"):
            self.schedule_key(key, task)
            return
        title = clip(one_line(task["task"]), 30)
        if key == "p":
            resume = task["state"] in ("paused", "failed")
            if self.send({"cmd": "queue-resume" if resume else "queue-pause", "id": task["id"]}):
                self.tell(("resumed " if resume else "paused ") + title)
        elif key == "x":
            self.ask = {"kind": "confirm", "question": f"Cancel \"{title}\"?", "then": lambda: self.cancel(task)}

    def schedule_key(self, key, s):
        title = clip(one_line(s["title"]), 30)
        if key == "r":
            if self.send({"cmd": "schedule-run", "id": s["id"]}, timeout=30):
                self.tell("queued " + title)
        elif key == "p":
            if self.send({"cmd": "schedule-resume" if s["paused"] else "schedule-pause", "id": s["id"]}):
                self.tell(("resumed " if s["paused"] else "paused ") + title)
        elif key == "x":
            self.ask = {"kind": "confirm", "question": f"Remove the schedule \"{title}\"?",
                        "then": lambda: self.remove_schedule(s)}

    def remove_schedule(self, s):
        if self.send({"cmd": "schedule-remove", "id": s["id"]}):
            self.tell("removed " + clip(one_line(s["title"]), 30))
            self.detail = False

    def fleet_key(self, key):
        run = self.selected()
        if run is None or key not in ("y", "b", "d", "l", "x", "c", "a", "r"):
            return
        gate = run.get("gate") if run["status"] == "at-gate" else None
        scoped = self.scope_hold(run)
        if scoped and key in ("a", "b"):
            if key == "a":
                self.ask = {"kind": "note", "question": "Why accept them",
                            "then": lambda reason: self.fleet_send({"cmd": "fleet-accept-scope", "run": run["id"],
                                                                    "node": scoped, "reason": reason},
                                                                   "accepted; on to review"), "value": ""}
            else:
                self.ask = {"kind": "confirm", "question": "Send the slice to a new builder to undo them?",
                            "then": lambda: self.fleet_send({"cmd": "fleet-scope-back", "run": run["id"], "node": scoped},
                                                            "sent back to undo the extra files")}
            return
        if key == "r" and run["status"] == "held" and run.get("held_by") == "limits":
            limits = run.get("limits") or {}
            self.ask = {"kind": "note", "question": "Budget $ (and steps)",
                        "value": f"{limits.get('budget') or 0} {limits.get('max_steps') or 30}",
                        "then": lambda text: self.raise_limits(run, text)}
            return
        title = clip(one_line(run["goal"]), 30)
        if key == "x" and run["status"] not in ("done", "cancelled"):
            self.ask = {"kind": "confirm", "question": f"Cancel the run \"{title}\"?",
                        "then": lambda: self.fleet_send({"cmd": "fleet-cancel", "run": run["id"]}, "cancelled " + title)}
        elif key == "c" and run["status"] == "done" and not run.get("closed"):
            self.ask = {"kind": "confirm", "question": "Close it? Git is checked first",
                        "then": lambda: self.fleet_send({"cmd": "fleet-close", "run": run["id"]},
                                                        "closed: its work is on " + str(run.get("branch") or "the folder"))}
        elif key == "y" and gate:
            self.detail = True  # the plan on screen before saying yes
            question = "Approve this plan? Builders start" if gate == "plan" else "Approve merging the slices?"
            self.ask = {"kind": "confirm", "question": question,
                        "then": lambda: self.fleet_send({"cmd": "fleet-approve", "run": run["id"], "gate": gate},
                                                        f"approved the {gate}")}
        elif key == "b" and gate == "plan":
            self.ask = {"kind": "note", "question": "What should change", "value": "",
                        "then": lambda note: self.fleet_send({"cmd": "fleet-send-back", "run": run["id"], "note": note},
                                                             "sent the plan back")}
        elif key == "d" and gate == "plan":
            slices = [s["id"] for s in (fleet.live_plan(run) or {}).get("slices") or []]
            self.ask = {"kind": "choice", "question": "Drop slice", "options": slices,
                        "then": lambda sid: self.fleet_send({"cmd": "fleet-drop", "run": run["id"], "slice": sid},
                                                            f"dropped {sid}")}
        elif key == "l" and gate == "plan":
            self.ask = {"kind": "choice", "question": "Run it as", "options": ["single-loop", "diamond"],
                        "then": lambda shape: self.fleet_send({"cmd": "fleet-shape", "run": run["id"], "shape": shape},
                                                              f"shape: {shape}")}

    @staticmethod
    def scope_hold(run):
        """The builder whose extra files hold the run, or None."""
        held = run.get("held_by") if run["status"] == "held" else None
        n = run["nodes"].get(held) if held else None
        return held if n and (n.get("scope") or {}).get("extra") and not n["scope"].get("accepted") else None

    def raise_limits(self, run, text):
        parts = text.replace("$", "").split()
        try:
            budget = float(parts[0]) if parts else None
            steps = int(parts[1]) if len(parts) > 1 else None
        except ValueError:
            self.tell("type the budget in US$, then the steps, e.g. 10 40", "urgent")
            return
        self.fleet_send({"cmd": "fleet-limits", "run": run["id"], "budget": budget, "max_steps": steps},
                        "limits changed")

    def fleet_send(self, payload, done):
        response = self.send(payload, timeout=30)
        if response:
            self.fleets[response["run"]["id"]] = response["run"]
            note = response["run"].get("shape_note") if payload["cmd"] == "fleet-shape" else None
            self.tell(note or done)

    def answer(self, key):
        ask = self.ask
        if key == "esc" or (ask["kind"] == "confirm" and key == "n"):
            self.ask = None
            return
        if ask["kind"] == "note":
            if key == "enter" and ask["value"].strip():
                self.ask = None
                ask["then"](ask["value"].strip())
            elif key == "backspace":
                ask["value"] = ask["value"][:-1]
            elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                ask["value"] += key
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

    def new_task(self, schedule=False):
        """The new-task form: task, folder, and for a schedule, when."""
        self.ask = {"kind": "text", "field": "task", "question": "Task", "value": "", "task": None,
                    "folder": self.default_folder(), "schedule": schedule}

    def form_next(self):
        ask = self.ask
        if ask["field"] == "task":
            if not ask["value"].strip():
                return
            ask.update(field="folder", question="Folder", task=ask["value"].strip(), value=present.place(ask["folder"]))
            return
        if ask["field"] == "folder":
            folder = Path(ask["value"].strip() or "~").expanduser()
            if not folder.is_dir():
                self.tell(f"no folder {folder}", "urgent")
                return
            ask["folder"] = folder
            if ask["schedule"]:
                ask.update(field="when", question="When", value="")
                self.tell("daily 09:00, weekdays 09:00, mon,thu 18:30, every 6h", "dim")
                return
        else:  # when
            try:
                schedules.parse_when(ask["value"])
            except schedules.ScheduleError as e:
                self.tell(str(e), "urgent")
                return
            ask["when"] = ask["value"].strip()
        task, folder, when = ask["task"], ask["folder"], ask.get("when")
        add = (lambda agent: self.add_schedule(task, folder, when, agent)) if when else \
            (lambda agent: self.add_task(task, folder, agent))
        if len(self.agents) == 1:
            self.ask = None
            add(self.agents[0])
            return
        self.ask = {"kind": "choice", "question": "Start it with", "options": list(self.agents), "then": add}

    def add_task(self, task, folder, agent):
        item = {"task": task, "cwd": str(folder.resolve()), "worktree": None, "extra": [], "agent": agent,
                **launch.agent_environment(agent)}
        if self.send({"cmd": "queue-add", "item": item}, timeout=30):
            self.tell("queued " + clip(one_line(task), 30))
            self.switch("queue")
            self.index["queue"] = len(self.rows()) - 1

    def add_schedule(self, task, folder, when, agent):
        item = {"task": task, "cwd": str(folder.resolve()), "worktree": None, "extra": [], "agent": agent,
                **launch.agent_environment(agent)}
        response = self.send({"cmd": "schedule-add", "when": when, "items": [item], "task": task})
        if response:
            self.tell(f"scheduled: {schedules.describe(response['schedule']['when'])}")
            self.switch("queue")
            self.index["queue"] = len(self.schedules.get("schedules") or []) - 1

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
        elif self.tab == "fleets":
            self.fleet_list(screen, body)
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
        needing = sum(1 for s in self.fleets.values() if s["status"] in ("at-gate", "held"))
        queued = len(self.queue.get("tasks") or [])
        right = None
        if self.away_active():
            right = (" away " if self.away["away"] else " here ", "urgent" if self.away["away"] else "dim", "a")
        fleets = f" {needing}!" if needing else f" {len(self.fleets)}"
        # The full labels, then a short Fleets tab, then short labels all round, whichever fits a phone.
        for labels in ((" Sessions" + f" {len(self.sessions)}" + (f" ({waiting}!)" if waiting else "") + " ",
                        f" Queue {queued} ", " Fleets" + fleets + " "),
                       (" Sessions" + f" {len(self.sessions)}" + (f" ({waiting}!)" if waiting else "") + " ",
                        f" Queue {queued} ", " F" + fleets + " "),
                       (f" S {len(self.sessions)}" + (f" {waiting}!" if waiting else "") + " ", f" Q {queued} ",
                        " F" + fleets + " ")):
            if sum(len(t) for t in labels) + 2 + len(right[0] if right else "") + 1 <= screen.width:
                break
        tabs = []
        for tab, label in zip(TABS, labels):
            tabs += [(label, "tab-on" if self.tab == tab else "tab", ("tab", tab)), (" ", "")]
        screen.line(*tabs[:-1], right=right)
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
        if self.schedules.get("schedules") and not self.schedules.get("enabled", True):
            screen.line((clip("schedules are off (schedules.enabled)", screen.width), "urgent"))
            room -= 1
        rows = self.rows()
        if not rows:
            screen.line(("No queued tasks. n adds one,", "dim"))
            screen.line(("w a schedule.", "dim"))
            return
        self.selected()
        first, fit = self.visible(len(rows), room)
        for i in range(first, min(len(rows), first + fit)):
            t = rows[i]
            chosen = i == self.index["queue"]
            if t.get("schedule_row"):
                self.schedule_row(screen, t, i, chosen)
                continue
            number = i + 1 - len(self.schedules.get("schedules") or [])
            screen.line((("▸ " if chosen else "  ") + f"{number}. " + one_line(t["task"]),
                         "chosen" if chosen else "bold"))
            screen.row_hit(("row", i))
            word = TASK_WORD.get(t["state"], t["state"])
            if t.get("error"):
                word += ": " + one_line(t["error"])
            screen.line((f"  {present.project(t.get('cwd'))} · {t.get('agent') or 'claude'} · ", "dim"),
                        (word, TASK_STYLE.get(t["state"], "dim") or "dim"))
            screen.row_hit(("row", i))

    def schedule_row(self, screen, s, i, chosen):
        """A schedule in the queue tab: its title and next time, then when,
        where and how its last run went."""
        width = screen.width
        when = "paused" if s["paused"] else present.day_time(s.get("next"), self.now())
        tail = f" {when} "
        screen.line((("▸ " if chosen else "  ") + "⟳ " + clip(one_line(s["title"]), width - len(tail) - 5),
                     "chosen" if chosen else "bold"), right=(tail, "dim" if s["paused"] else "work"))
        screen.row_hit(("row", i))
        status = s.get("status") or ""
        last = f" · last: {status}" if s.get("last") and status else ""
        bad = status.startswith(("failed", "held", "could not"))
        screen.line(("  " + clip(f"{s['whenText']} · {present.project(s.get('cwd'))}{last}", width - 2),
                     "urgent" if bad else "dim"))
        screen.row_hit(("row", i))

    def fleet_list(self, screen, room):
        rows = self.rows()
        if not rows:
            screen.line(("No fleet runs.", "dim"))
            return
        self.selected()
        first, fit = self.visible(len(rows), room)
        width = screen.width
        for i in range(first, min(len(rows), first + fit)):
            run = rows[i]
            chosen = i == self.index["fleets"]
            word = RUN_WORD.get(run["status"], run["status"])
            if run["status"] == "at-gate":
                word = f"{run.get('gate')} gate"
            elif run.get("closed"):
                word = "closed"
            tail = f" {word} "
            screen.line((("▸ " if chosen else "  ") + clip(one_line(run["goal"]), width - len(tail) - 2),
                         "chosen" if chosen else "bold"), right=(tail, RUN_STYLE.get(run["status"], "")))
            screen.row_hit(("row", i))
            done = sum(1 for n in run["nodes"].values() if n["status"] == "done")
            waiting = [n["id"] for n in run["nodes"].values() if n.get("waiting") and n["status"] == "running"]
            stalled = [n["id"] for n in run["nodes"].values() if n.get("stalled") and n["status"] == "running"]
            second = run.get("reason") if run["status"] == "held" else \
                (f"{waiting[0]} waits for you" if waiting else f"{stalled[0]} looks stalled" if stalled else
                 f"{present.project(run.get('folder'))} · {run.get('shape') or run.get('fleet')} · {done} steps done")
            screen.line(("  " + clip(one_line(second or ""), width - 2), "urgent" if run["status"] == "held" else "dim"))
            screen.row_hit(("row", i))

    def fleet_lines(self, run, width):
        """A run's details: its plan when at the plan gate, else where each node stands."""
        lines = []

        def field(label, value, style=""):
            if value:
                for n, text in enumerate(textwrap.wrap(one_line(str(value)), width - 2) or [""]):
                    lines.append(((label + ": " if n == 0 else "  ") + text if label else text, style))

        field("", run["goal"], "bold")
        state = RUN_WORD.get(run["status"], run["status"])
        if run["status"] == "at-gate":
            state = f"waiting at the {run.get('gate')} gate"
        field("", state, RUN_STYLE.get(run["status"], ""))
        field("held", run.get("reason") if run["status"] == "held" else None, "urgent")
        if run["status"] == "done":
            field("", "closed: merge its branch when you are ready" if run.get("closed")
                  else "finished: c checks it against git and closes it", "dim")
        field("branch", run.get("branch"))
        limits = run.get("limits") or {}
        spent = sum((n.get("cost") or {}).get("usd") or 0 for n in run["nodes"].values())
        field("spent", f"${spent:.2f}" + (f" of ${limits['budget']}" if limits.get("budget") else "")
              + f" · {len(run['nodes'])} of {limits.get('max_steps') or 30} steps", "dim")
        for n in run["nodes"].values():
            if n["status"] == "running" and (n.get("waiting") or n.get("stalled")):
                field(n["id"], "waits for you (its session)" if n.get("waiting") else "no sign of life lately",
                      "urgent")
        scoped = self.scope_hold(run)
        if scoped:
            field("", "a accepts the extra files (with your reason) and the slice goes to review; b sends it to a new "
                      "builder to undo them.", "dim")
        field("in", present.place(run.get("folder")))
        plan = fleet.live_plan(run)
        if plan:
            field("shape", run.get("shape"))
            field("", run.get("shape_note"), "work")
            if run.get("gate") == "plan":
                field("why", plan.get("rationale"), "dim")
            for s in plan["slices"]:
                lines.append(("", ""))
                field(s["id"], s["intent"], "bold")
                field("files", ", ".join(s["files"]))
                field("done when", s["done_when"])
                field("risk", f"{s['risk']}: {s['risk_why']}", "urgent" if s["risk"] == "high" else "dim")
                for role in ("builder", "reviewer"):
                    n = fleet.latest(run, role, s["id"])
                    if n:
                        verdict = n["result"]["verdict"] if role == "reviewer" and n["result"] else n["status"]
                        field(role, f"{verdict} (try {n['attempt']})", "dim")
            if run.get("dropped"):
                field("dropped", ", ".join(run["dropped"]), "dim")
            if run.get("gate") == "plan":
                lines.append(("", ""))
                field("not doing", "; ".join(plan.get("not_doing") or []) or "(nothing named)")
                field("approve", plan.get("approve"), "bold")
                field("", "y approves (builders start), b sends it back with a note, d drops a slice, l picks the "
                          "shape, x cancels.", "dim")
        else:
            for n in run["nodes"].values():
                field(n["id"], n["status"] + (f": {n['error']}" if n.get("error") else ""), "dim")
        field("id", run["id"])
        return lines

    def details(self, screen, room):
        item = self.selected()
        width = screen.width
        if self.tab == "fleets":
            lines = self.fleet_lines(item, width)
            self.scroll = max(0, min(self.scroll, len(lines) - room))
            for text, style in lines[self.scroll:self.scroll + room]:
                screen.line((text, style))
            return
        lines = []  # (text, style)

        def field(label, value, style=""):
            if value:
                for n, text in enumerate(textwrap.wrap(one_line(value), width - 2) or [""]):
                    lines.append(((label + ": " if n == 0 else "  ") + text if label else text, style))

        if item.get("schedule_row"):
            lines.append((item["title"], "bold"))
            lines.append(("paused" if item["paused"] else "next " + present.day_time(item.get("next"), self.now()),
                          "dim" if item["paused"] else "work"))
            field("when", item["whenText"])
            if item.get("last"):
                status = item.get("status") or item.get("last_note") or ""
                field("last", f"{present.day_time(item['last'], self.now())}: {status}",
                      "urgent" if status.startswith(("failed", "held", "could not")) else "")
            if item.get("name"):
                field("task", item.get("task"))
            field("recipe", item.get("recipe"))
            field("in", present.place(item.get("cwd")))
            first = (item.get("items") or [{}])[0]
            field("agent", " · ".join(x for x in (first.get("agent") or "claude", first.get("model")) if x))
            field("", "r queues it now, whatever the time; p pauses or resumes it; x removes it (what it already "
                      "queued stays queued).", "dim")
            field("id", item["id"][:8])
        elif self.tab == "sessions":
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
            if ask["kind"] == "note":
                return [("⏎ Send", "enter"), ("esc Cancel", "esc")]
            last = "Schedule it" if ask["field"] == "when" else \
                "Next" if ask["field"] == "task" or ask["schedule"] else "Queue it"
            return [("⏎ " + last, "enter"), ("esc Cancel", "esc")]
        items = []
        if self.selected():
            items.append(("⏎ Back" if self.detail else "⏎ More", "enter"))
        if self.tab == "sessions" and self.selected():
            if self.pending(self.selected()):
                items += [("y Allow…", "y"), ("x Deny", "x")]
            items += [("d Dismiss", "d"), ("s Stop", "s"), ("h Hand off", "h")]
        if self.tab == "fleets" and self.selected():
            run = self.selected()
            gate = run.get("gate") if run["status"] == "at-gate" else None
            if gate:
                items.append(("y Approve…", "y"))
            if gate == "plan":
                items += [("b Send back", "b"), ("d Drop slice", "d"), ("l Shape", "l")]
            if self.scope_hold(run):
                items += [("a Accept…", "a"), ("b Send back…", "b")]
            if run["status"] == "held" and run.get("held_by") == "limits":
                items.append(("r Raise limits", "r"))
            if run["status"] not in ("done", "cancelled"):
                items.append(("x Cancel run", "x"))
            if run["status"] == "done" and not run.get("closed"):
                items.append(("c Close…", "c"))
        if self.tab == "queue":
            task = self.selected()
            if task and task.get("schedule_row"):
                items += [("r Run now", "r"), ("p Resume" if task["paused"] else "p Pause", "p"), ("x Remove", "x")]
            elif task:
                items.append(("p Resume" if task["state"] in ("paused", "failed") else "p Pause", "p"))
                items.append(("x Cancel", "x"))
            items.append(("H Release" if self.queue.get("held") else "H Hold", "H"))
        items.append(("n New task", "n"))
        if self.tab == "queue":
            items.append(("w New schedule", "w"))
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
            if ask["kind"] in ("text", "note"):
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
