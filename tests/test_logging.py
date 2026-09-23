import io
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra import daemon, log
from omaorchestra.registry import Registry


class FormatTest(unittest.TestCase):
    def tearDown(self):
        log.logger.handlers[:] = []

    def emit(self, journal, level, *args, **kv):
        out = io.StringIO()
        log.setup(verbose=True, stream=out, journal=journal)
        log.event(level, *args, **kv)
        return out.getvalue()

    def test_journal_lines_carry_priority(self):
        self.assertEqual(self.emit(True, logging.INFO, "started", version="1"), "<6>started version=1\n")
        self.assertTrue(self.emit(True, logging.WARNING, "bad request").startswith("<4>"))
        self.assertTrue(self.emit(True, logging.ERROR, "bad config").startswith("<3>"))
        self.assertTrue(self.emit(True, logging.DEBUG, "request").startswith("<7>"))

    def test_console_lines_have_time_and_level(self):
        self.assertRegex(self.emit(False, logging.INFO, "started"), r"^\d\d:\d\d:\d\d INFO started\n$")

    def test_under_journal_needs_the_stream_itself(self):
        with tempfile.TemporaryFile() as f:
            st = os.fstat(f.fileno())
            self.assertTrue(log.under_journal(f, {"JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino}"}))
            # Inherited from a parent whose stderr was the journal: not ours.
            self.assertFalse(log.under_journal(f, {"JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino + 1}"}))
            self.assertFalse(log.under_journal(f, {}))
            self.assertFalse(log.under_journal(f, {"JOURNAL_STREAM": "junk"}))
        self.assertFalse(log.under_journal(io.StringIO(), {"JOURNAL_STREAM": "1:2"}))

    def test_fields_quote_awkward_values_and_skip_none(self):
        self.assertEqual(log.fields(a="x", b="has space", c=None, d='q"', e=""), 'a=x b="has space" d="q\\"" e=""')


class DaemonEventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), is_alive=lambda p, s: False)

    def tearDown(self):
        self.tmp.cleanup()

    def update(self, status, **extra):
        return self.d.handle({"cmd": "update", "session_id": "s1", "agent": "claude", "status": status, **extra})

    def messages(self, action, level="INFO"):
        with self.assertLogs("omaorchestra", level=level) as logs:
            action()
        return [r.getMessage() for r in logs.records]

    def test_session_lifecycle(self):
        started = self.messages(lambda: self.update("idle", cwd="/w/proj", pid=5, pid_start=50))
        self.assertEqual(started, ["session started id=s1 agent=claude status=idle pid=5 cwd=/w/proj"])
        changed = self.messages(lambda: self.update("needs-input", message="Allow Bash?"))
        self.assertEqual(changed, ['session changed id=s1 status=needs-input message="Allow Bash?"'])
        ended = self.messages(lambda: self.d.handle({"cmd": "remove", "session_id": "s1"}))
        self.assertEqual(ended, ["session ended id=s1 reason=session-end"])

    def test_repeated_status_is_quiet_at_info(self):
        self.update("working")
        with self.assertNoLogs("omaorchestra", level="INFO"):
            self.update("working")

    def test_prune_logs_the_reason(self):
        self.update("working", pid=5, pid_start=50)
        self.assertEqual(self.messages(self.d.prune), ["session ended id=s1 reason=process-gone pid=5"])

    def test_bad_command_warns(self):
        self.assertEqual(self.messages(lambda: self.d.handle({"cmd": "nope"}), "WARNING"),
                         ["bad request error=\"unknown command: nope\""])

    def test_verbose_logs_requests_without_messages(self):
        msgs = self.messages(lambda: self.update("needs-input", message="secret-ish text"), "DEBUG")
        self.assertTrue(msgs[0].startswith("request cmd=update session_id=s1"))
        self.assertNotIn("secret-ish", msgs[0])


class ShutdownTest(unittest.TestCase):
    def test_sigterm_stops_cleanly_and_removes_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "o.sock"
            env = {**os.environ, "OMAORCHESTRA_SOCKET": str(sock), "OMAORCHESTRA_STATE_DIR": str(Path(tmp) / "state"),
                   "OMAORCHESTRA_CONFIG": str(Path(tmp) / "none.toml"), "PYTHONUNBUFFERED": "1"}
            env.pop("JOURNAL_STREAM", None)
            proc = subprocess.Popen([str(ROOT / "bin" / "omaorchestra"), "daemon"], env=env,
                                    stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(50):
                    if sock.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(sock.exists(), "daemon did not start")
                proc.send_signal(signal.SIGTERM)
                _, err = proc.communicate(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()
            self.assertEqual(proc.returncode, 0, err)
            self.assertIn("INFO started version=", err)
            self.assertIn("INFO stopping signal=SIGTERM", err)
            self.assertIn("INFO stopped", err)
            self.assertFalse(sock.exists())


if __name__ == "__main__":
    unittest.main()
