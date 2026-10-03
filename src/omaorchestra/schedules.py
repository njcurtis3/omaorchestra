"""Tasks that queue themselves on a timetable.

A schedule holds the queue items to add (one task, or a recipe's steps as a
chain) and when: on some days at a time (`daily 09:00`, `weekdays 09:00`,
`mon,thu 18:30`) or every so often (`every 6h`, `every 30m`). The daemon
looks twice a minute and adds a due schedule's items to the queue, where
they wait for a slot like any other task: the queue's limits, budget and
usage holds all apply. A schedule only ever queues work; nothing it starts
is approved on the user's behalf.

Runs missed while the machine was off or asleep run once when it is back,
never once per missed time. A schedule whose last run is still queued or
running skips its time rather than piling up.
"""

import datetime
import json
import os
import re
import time
import uuid

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_FULL_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MIN_INTERVAL = 15  # minutes
MAX_INTERVAL = 7 * 24 * 60

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_EVERY = re.compile(r"^every\s+(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)$")


class ScheduleError(Exception):
    pass


def parse_when(text):
    """`daily 09:00`, `weekdays 9:00`, `weekends 10:00`, `mon,thu 18:30`,
    `every 6h` or `every 30m`, as a dict for `next_run` and `describe`."""
    words = " ".join(str(text or "").lower().replace(" at ", " ").split())
    every = _EVERY.match(words)
    if every:
        n, unit = int(every.group(1)), every.group(2)
        minutes = n * 60 if unit.startswith("h") else n
        if not MIN_INTERVAL <= minutes <= MAX_INTERVAL:
            raise ScheduleError(f"every {minutes} minutes: must be from {MIN_INTERVAL} minutes to 7 days")
        return {"every": minutes}
    parts = words.rsplit(" ", 1)
    if len(parts) != 2 or not _TIME.match(parts[1]):
        raise ScheduleError(f"{text!r}: say when like `daily 09:00`, `weekdays 09:00`, `mon,thu 18:30` "
                            "or `every 6h`")
    days_text, clock = parts
    hour, minute = (int(x) for x in clock.split(":"))
    if days_text in ("daily", "every day", "everyday"):
        days = list(range(7))
    elif days_text == "weekdays":
        days = list(range(5))
    elif days_text == "weekends":
        days = [5, 6]
    else:
        days = []
        for name in re.split(r"[,\s]+", days_text):
            # mon, Monday, tues, thurs...: at least three letters of a day.
            match = [i for i, full in enumerate(_FULL_DAYS) if len(name) >= 3 and full.startswith(name)]
            if not match:
                raise ScheduleError(f"{name!r} is not a day (mon, tue, wed, thu, fri, sat, sun, daily, weekdays, "
                                    "weekends)")
            days.append(match[0])
        days = sorted(set(days))
    return {"days": days, "time": f"{hour:02d}:{minute:02d}"}


def describe(when):
    """`weekdays at 09:00`, `Mon, Thu at 18:30`, `every 6h`."""
    if "every" in when:
        m = when["every"]
        return f"every {m // 60}h" if m % 60 == 0 else f"every {m}m"
    days = when["days"]
    if days == list(range(7)):
        which = "daily"
    elif days == list(range(5)):
        which = "weekdays"
    elif days == [5, 6]:
        which = "weekends"
    else:
        which = ", ".join(DAY_NAMES[d].capitalize() for d in days)
    return f"{which} at {when['time']}"


def next_run(when, after, anchor=None):
    """The first time strictly after `after` (epoch seconds, local time) that
    `when` comes round. An interval counts from `anchor` (when it was made)."""
    if "every" in when:
        step = when["every"] * 60
        start = anchor if anchor is not None else after
        if start > after:
            return start
        return start + (int((after - start) // step) + 1) * step
    hour, minute = (int(x) for x in when["time"].split(":"))
    day = datetime.datetime.fromtimestamp(after).date()
    for offset in range(8):
        d = day + datetime.timedelta(days=offset)
        if d.weekday() not in when["days"]:
            continue
        at = datetime.datetime(d.year, d.month, d.day, hour, minute).timestamp()
        if at > after:
            return at
    raise ScheduleError("no day to run on")  # parse_when never makes an empty day list


class Schedules:
    def __init__(self, path):
        self.path = path
        self.items = []
        try:
            data = json.loads(path.read_text())
            self.items = [s for s in data.get("schedules", []) if isinstance(s, dict) and s.get("id")]
        except (OSError, ValueError, AttributeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schedules": self.items}, indent=2))
        os.replace(tmp, self.path)

    def find(self, key):
        matches = [s for s in self.items if s["id"].startswith(key)]
        if not matches:
            raise ScheduleError(f"no schedule matching {key}")
        if len(matches) > 1:
            raise ScheduleError(f"{key} matches {len(matches)} schedules; give more of the id")
        return matches[0]

    def add(self, when_text, items, name=None, task=None, now=None):
        """A schedule queueing `items` (in order, each after the one before).
        `task` is the user's own text, for lists (a recipe's steps expand it)."""
        now = time.time() if now is None else now
        when = parse_when(when_text)
        if not items or not all(isinstance(i, dict) and str(i.get("task", "")).strip() for i in items):
            raise ScheduleError("a schedule needs a task")
        cwd = items[0].get("cwd")
        if not cwd or not os.path.isdir(cwd):
            raise ScheduleError(f"{cwd} is not a directory")
        item = {"id": str(uuid.uuid4()), "name": (name or "").strip() or None, "when": when,
                "task": (task or items[0]["task"]).strip(), "recipe": items[0].get("recipe"),
                "items": items, "paused": False, "created": now, "next": next_run(when, now, anchor=now),
                "last": None, "last_note": None, "tasks": [], "sessions": [], "outcome": None}
        self.items.append(item)
        self.save()
        return item

    def remove(self, key):
        item = self.find(key)
        self.items.remove(item)
        self.save()
        return item

    def set_paused(self, key, paused, now=None):
        item = self.find(key)
        item["paused"] = paused
        if not paused:
            # Resumed: from now on, not the times it was paused through.
            item["next"] = next_run(item["when"], time.time() if now is None else now, anchor=item["created"])
        self.save()
        return item

    def due(self, now):
        return [s for s in self.items if not s["paused"] and s["next"] <= now]

    def ran(self, item, now, task_ids=None, note=None):
        """Record a time come round: what it queued (or why it did not), and
        the next time, counted from now so missed times are not made up."""
        item["last"] = now
        item["last_note"] = note
        if task_ids:
            item["tasks"], item["sessions"], item["outcome"] = list(task_ids), [], None
        item["next"] = next_run(item["when"], now, anchor=item["created"])
        self.save()

    def by_task(self, task_id):
        return next((s for s in self.items if task_id in s["tasks"]), None)

    def by_session(self, session_id):
        return next((s for s in self.items if session_id in s["sessions"]), None)

    @staticmethod
    def title(item):
        if item.get("name"):
            return item["name"]
        return (f"{item['recipe']}: " if item.get("recipe") else "") + item["task"].splitlines()[0]
