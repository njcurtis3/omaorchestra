import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import client, control, top

ROOT = Path(__file__).resolve().parent.parent
NOW = 10_000


def session(sid, status, cwd, since=NOW - 180, **extra):
    return {"id": sid, "agent": "claude", "status": status, "cwd": cwd, "status_since": since, "updated": since,
            "started": since, **extra}


def task(tid, text, state="pending", **extra):
    return {"id": tid, "task": text, "cwd": "/w/api", "state": state, **extra}


SNAPSHOT = {
    "ok": True,
    "sessions": [session("s-idle", "idle", "/w/docs", since=NOW - 7200),
                 session("s-wait", "needs-input", "/w/website", message="Allow Bash: npm test?", pid=1, pid_start=2),
                 session("s-work", "working", "/w/tries", title="Retry flaky uploads")],
    "queue": {"held": False, "busy": 2, "limit": 2, "blocked": None,
              "tasks": [task("t1", "Refactor the parser"), task("t2", "Write docs", state="paused")]},
    "away": {"mode": "auto", "away": False, "reason": None, "push": True, "after": 10},
}


class Daemon:
    """Answers requests like omaorchestrad, and remembers them."""

    def __init__(self):
        self.requests = []
        self.down = False

    def __call__(self, payload, timeout=1.0):
        if self.down:
            raise client.DaemonUnavailable("gone")
        self.requests.append(payload)
        cmd = payload["cmd"]
        if cmd == "away":
            return {"ok": True, "away": {**SNAPSHOT["away"], "mode": payload["mode"], "away": payload["mode"] == "on"}}
        if cmd == "queue-add":
            tasks = SNAPSHOT["queue"]["tasks"] + [task("t3", payload["item"]["task"])]
            return {"ok": True, "item": tasks[-1], "queue": {**SNAPSHOT["queue"], "tasks": tasks}}
        if cmd == "handoff":
            return {"ok": True, "session_id": "new", "agent": payload["agent"], "stopped": False}
        if cmd == "queue-cancel" and payload["id"] == "gone":
            return {"ok": False, "error": "no queued task gone"}
        return {"ok": True}


class TopTest(unittest.TestCase):
    def setUp(self):
        self.daemon = Daemon()
        self.stopped = []
        self.t = top.Top(request=self.daemon, stop=self.stopped.append, wait=lambda s: True, now=lambda: NOW,
                         agents=["claude", "codex"], background=lambda work: work())
        self.t.apply(SNAPSHOT)

    def screen(self, width=40, height=20):
        return self.t.render(width, height).text()

    def tap(self, label):
        """Tap the first button or row showing `label`."""
        screen = self.t.render(40, 20)
        for y, line in enumerate(screen.lines):
            for x, text, _ in line:
                if label in text:
                    key = self.t.click(y, x + text.index(label))
                    self.assertIsNotNone(key, f"{label!r} is not tappable")
                    self.t.key(key)
                    return
        self.fail(f"no {label!r} on screen:\n{screen.text()}")

    def test_sessions_waiting_first_and_fits_a_phone(self):
        text = self.screen()
        lines = text.splitlines()
        self.assertTrue(all(len(line) <= 40 for line in lines))
        self.assertEqual(len(lines), 20)
        self.assertIn("Sessions 3 (1!)", lines[0])
        self.assertTrue(lines[0].endswith(" here "))
        order = [name for name in ("website", "tries", "docs") if name in text]
        self.assertEqual(order, ["website", "tries", "docs"])
        self.assertLess(text.index("website"), text.index("tries"))
        self.assertIn("Allow Bash: npm test?", text)
        self.assertIn("Retry flaky uploads", text)
        self.assertIn("waiting  3m", text)
        self.assertIn(" d Dismiss ", text)

    def test_too_small(self):
        self.assertIn("too small", self.screen(20, 5))

    def test_moving_and_details(self):
        self.t.key("down")
        self.assertEqual(self.t.selected()["project"], "tries")
        self.t.key("enter")
        text = self.screen()
        self.assertIn("working for 3m", text)
        self.assertIn("task: Retry flaky uploads", text)
        self.t.key("esc")
        self.assertNotIn("task: Retry", self.screen())

    def test_tapping_a_row_selects_it_then_opens_it(self):
        self.tap("docs")
        self.assertEqual(self.t.selected()["project"], "docs")
        self.assertFalse(self.t.detail)
        self.tap("docs")
        self.assertTrue(self.t.detail)

    def test_dismiss(self):
        self.tap("d Dismiss")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "remove", "session_id": "s-wait", "reason": "dismissed"})
        self.assertIn("dismissed website", self.screen())

    def test_stop_asks_first(self):
        self.t.key("s")
        self.assertIn("Stop the agent for website?", self.screen())
        self.t.key("n")
        self.assertEqual(self.stopped, [])
        self.tap("s Stop")
        self.tap("y Yes")
        self.assertEqual([s["id"] for s in self.stopped], ["s-wait"])
        self.assertEqual(self.daemon.requests[-1], {"cmd": "list"})
        self.assertIn("stopped website", self.screen())

    def test_stop_failure_is_shown(self):
        def refuse(session):
            raise control.ControlError("session s-wait has no recorded process")
        self.t.stop_agent = refuse
        self.t.key("s")
        self.t.key("y")
        self.assertIn("no recorded process", self.screen())

    def test_handoff_picks_an_agent(self):
        self.t.key("h")
        self.assertIn("Hand website off to:", self.screen())
        self.tap("2 codex")
        request = self.daemon.requests[-1]
        self.assertEqual((request["cmd"], request["session_id"], request["agent"]), ("handoff", "s-wait", "codex"))
        self.assertIn("handed website to codex", self.screen())

    def test_queue_tab(self):
        self.tap("Queue 2")
        text = self.screen()
        self.assertIn("2 of 2 agents busy · queue running", text)
        self.assertIn("1. Refactor the parser", text)
        self.assertIn("api · claude · waiting its turn", text)
        self.assertIn(" p Pause ", text)
        self.t.key("p")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "queue-pause", "id": "t1"})
        self.t.key("down")
        self.assertIn(" p Resume ", self.screen())
        self.t.key("p")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "queue-resume", "id": "t2"})
        self.tap("H Hold")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "queue-hold"})

    def test_cancel_asks_first_and_shows_errors(self):
        self.t.switch("queue")
        self.t.key("x")
        self.t.key("y")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "queue-cancel", "id": "t1"})
        self.t.cancel({"id": "gone", "task": "x"})
        self.assertIn("no queued task gone", self.screen())

    def test_new_task_form(self):
        with tempfile.TemporaryDirectory() as folder:
            self.t.key("n")
            for ch in "Fix it":
                self.t.key(ch)
            self.t.key("backspace")
            self.t.key("t")
            self.assertIn("Task: Fix it", self.screen())
            self.t.key("enter")
            self.assertIn("Folder: /w/website", self.screen(), "the selected session's folder is offered")
            self.t.ask["value"] = folder
            self.t.key("enter")
            self.tap("1 claude")
            item = self.daemon.requests[-1]["item"]
            self.assertEqual((item["task"], item["cwd"], item["agent"]),
                             ("Fix it", str(Path(folder).resolve()), "claude"))
            self.assertEqual(self.t.tab, "queue")
            self.assertEqual(self.t.selected()["id"], "t3")

    def test_new_task_needs_a_real_folder(self):
        self.t.key("n")
        self.t.key("x")
        self.t.key("enter")
        self.t.ask["value"] = "/no/such/folder"
        self.t.key("enter")
        self.assertIn("no folder /no/such/folder", self.screen())
        self.assertEqual(self.t.ask["field"], "folder", "the form stays open to fix it")
        self.t.key("esc")
        self.assertIsNone(self.t.ask)

    def test_away_cycles(self):
        self.tap(" here ")
        self.assertEqual(self.daemon.requests[-1], {"cmd": "away", "mode": "on"})
        self.assertTrue(self.screen().splitlines()[0].endswith(" away "))

    def test_connecting_first(self):
        fresh = top.Top(request=self.daemon, now=lambda: NOW, agents=["claude"])
        self.assertIn("Connecting", fresh.render(40, 20).text())

    def test_daemon_down(self):
        self.t.apply({"event": "lost"})
        self.assertIn("not running", self.screen())
        self.daemon.down = True
        self.t.key("q")
        self.assertFalse(self.t.running)

    def test_live_updates(self):
        self.t.apply({"event": "removed", "id": "s-wait"})
        self.t.apply({"event": "session", "session": session("s-new", "needs-input", "/w/new", message="Continue?")})
        self.assertIn("Continue?", self.screen())
        self.assertNotIn("website", self.screen())

    def test_scrolls_to_keep_the_selection_in_view(self):
        self.t.apply({**SNAPSHOT, "sessions": [session(f"s{i}", "idle", f"/w/p{i:02}", since=NOW - i)
                                               for i in range(20)]})
        for _ in range(15):
            self.t.key("down")
        self.assertIn("▸ p15", self.screen())


@unittest.skipUnless(sys.platform.startswith("linux"), "needs a pty")
class TerminalTest(unittest.TestCase):
    def test_runs_in_a_terminal_and_quits(self):
        """The real curses screen, in a pseudo-terminal, against no daemon."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "40", "LINES": "20",
                   "OMAORCHESTRA_SOCKET": os.path.join(tmp, "none.sock"),
                   "OMAORCHESTRA_CONFIG": os.path.join(tmp, "c.toml"), "OMAORCHESTRA_STATE_DIR": tmp}
            pid, fd = pty.fork()
            if pid == 0:
                os.execve(str(ROOT / "bin" / "omaorchestra"), ["omaorchestra", "top"], env)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 20, 40, 0, 0))  # a phone-sized screen
            output = b""
            deadline = time.time() + 10
            # "Connecting…" first, then "not running" once the connection fails.
            while b"not running" not in output and time.time() < deadline:
                try:
                    if select.select([fd], [], [], 0.2)[0]:
                        output += os.read(fd, 65536)
                except OSError:
                    break
            os.write(fd, b"q")
            while time.time() < deadline:
                try:
                    if select.select([fd], [], [], 0.2)[0] and not os.read(fd, 65536):
                        break
                except OSError:
                    break
            _, status = os.waitpid(pid, 0)
            os.close(fd)
        self.assertIn(b"Sessions", output)
        self.assertIn(b"not running", output)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)


if __name__ == "__main__":
    unittest.main()
