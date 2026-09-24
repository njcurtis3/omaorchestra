"""Tasks waiting for a free agent slot.

The daemon owns the queue and starts the next pending task whenever fewer
than `tasks.max_parallel` agents are busy (working, or waiting for the user;
an idle agent has finished and does not hold a slot). The queue is saved in
the state directory so it survives a daemon restart.
"""

import json
import os
import time
import uuid

# pending: waits its turn; paused: skipped until resumed; failed: its launch
# failed (kept, with the error, until retried or cancelled).
STATES = ("pending", "paused", "failed")

# What a queued task records, so the daemon can start it later exactly as the
# user asked, in the user's environment. Only PATH is kept from that
# environment: the rest could hold secrets and is not needed.
FIELDS = ("task", "cwd", "model", "permission_mode", "worktree", "extra", "agent_bin", "path", "provider",
          "mcp_profile")


class QueueError(Exception):
    pass


class TaskQueue:
    def __init__(self, path):
        self.path = path
        self.held = False
        self.tasks = []
        try:
            data = json.loads(path.read_text())
            self.held = bool(data.get("held"))
            self.tasks = [t for t in data.get("tasks", []) if isinstance(t, dict) and t.get("id")]
        except (OSError, ValueError, AttributeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"held": self.held, "tasks": self.tasks}, indent=2))
        os.replace(tmp, self.path)

    def find(self, key):
        matches = [t for t in self.tasks if t["id"].startswith(key)]
        if not matches:
            raise QueueError(f"no queued task matching {key}")
        if len(matches) > 1:
            raise QueueError(f"{key} matches {len(matches)} queued tasks; give more of the id")
        return matches[0]

    def add(self, fields, paused=False):
        if not str(fields.get("task", "")).strip():
            raise QueueError("the task is empty")
        if not fields.get("cwd") or not os.path.isdir(fields["cwd"]):
            raise QueueError(f"{fields.get('cwd')} is not a directory")
        item = {k: fields.get(k) for k in FIELDS}
        item.update(id=str(uuid.uuid4()), state="paused" if paused else "pending", added=time.time(), error=None)
        self.tasks.append(item)
        self.save()
        return item

    def remove(self, key):
        item = self.find(key)
        self.tasks.remove(item)
        self.save()
        return item

    def move(self, key, position):
        """Move a task to `position` (0 = first; clamped to the queue)."""
        item = self.find(key)
        self.tasks.remove(item)
        self.tasks.insert(max(0, min(position, len(self.tasks))), item)
        self.save()
        return item

    def set_state(self, key, state):
        if state not in STATES:
            raise QueueError(f"unknown state {state}")
        item = self.find(key)
        item["state"] = state
        if state != "failed":
            item["error"] = None
        self.save()
        return item

    def fail(self, item, error):
        item["state"], item["error"] = "failed", error
        self.save()

    def next_pending(self):
        return next((t for t in self.tasks if t["state"] == "pending"), None)

    def snapshot(self, busy, limit, blocked=None):
        """`blocked` says why pending tasks are not starting despite free
        slots (a nearly used subscription limit), or is None."""
        return {"held": self.held, "busy": busy, "limit": limit, "blocked": blocked,
                "tasks": [dict(t) for t in self.tasks]}
