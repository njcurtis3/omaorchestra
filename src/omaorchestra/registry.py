import json
import os
import time
from pathlib import Path

STATUSES = ("idle", "working", "needs-input")


class Registry:
    """Known agent sessions, keyed by session id and persisted as JSON."""

    def __init__(self, path: Path):
        self.path = path
        self.sessions = {}
        if path.exists():
            try:
                self.sessions = json.loads(path.read_text())
            except (OSError, ValueError):
                self.sessions = {}

    def update(self, session_id, agent, status, cwd=None, message=None, pid=None, pid_start=None):
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        now = time.time()
        session = self.sessions.setdefault(session_id, {"id": session_id, "agent": agent, "started": now})
        session.update(status=status, updated=now, message=message)
        if cwd:
            session["cwd"] = cwd
        if pid is not None and pid_start is not None:
            session["pid"], session["pid_start"] = pid, pid_start
        self.save()
        return session

    def prune(self, is_alive):
        """Drop sessions whose agent process is gone; return them by id.

        Sessions without a recorded process are kept: there is nothing to
        check them against.
        """
        dead = {
            sid: s for sid, s in self.sessions.items()
            if "pid" in s and not is_alive(s["pid"], s["pid_start"])
        }
        for sid in dead:
            del self.sessions[sid]
        if dead:
            self.save()
        return dead

    def remove(self, session_id):
        removed = self.sessions.pop(session_id, None)
        if removed:
            self.save()
        return removed

    def list(self):
        return sorted(self.sessions.values(), key=lambda s: s["started"])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sessions, indent=2))
        os.replace(tmp, self.path)
