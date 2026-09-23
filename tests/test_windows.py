import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import windows
from omaorchestra.__main__ import pick_session

# pid -> (comm, ppid): claude 30 runs in bash 20 inside terminal 10
TREE = {30: ("claude", 20), 20: ("bash", 10), 10: ("foot", 1)}


def parent(pid):
    return TREE.get(pid)


def win(pid, address, workspace="1"):
    return {"pid": pid, "address": address, "class": "foot", "title": "t", "workspace": {"id": 1, "name": workspace}}


class FakeHyprctl:
    """Answers hyprctl calls; dispatches in `refuse` get an error."""

    def __init__(self, refuse=(), active=4):
        self.calls = []
        self.refuse = refuse
        self.active = active

    def __call__(self, argv, capture_output, text):
        args = argv[1:]
        self.calls.append(args)
        out = "ok"
        if args[0] == "activeworkspace":
            out = json.dumps({"id": self.active, "name": str(self.active)})
        elif args[0] == "dispatch" and any(r in args[1] for r in self.refuse):
            out = "error: bad syntax"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    def dispatches(self):
        return [c[1] for c in self.calls if c[0] == "dispatch"]


class FindWindowTest(unittest.TestCase):
    def test_walks_up_to_the_terminal(self):
        self.assertEqual(windows.find_window(30, [win(10, "0xa"), win(99, "0xb")], parent), (win(10, "0xa"), True))

    def test_agent_owning_a_window_directly(self):
        self.assertEqual(windows.find_window(30, [win(30, "0xa")], parent)[0]["address"], "0xa")

    def test_shared_process_is_a_guess(self):
        found = windows.find_window(30, [win(10, "0xa"), win(10, "0xb")], parent)
        self.assertEqual(found, (win(10, "0xa"), False))

    def test_no_window(self):
        self.assertIsNone(windows.find_window(30, [win(99, "0xb")], parent))
        self.assertIsNone(windows.find_window(12345, [win(99, "0xb")], parent))

    def test_cycle_does_not_hang(self):
        self.assertIsNone(windows.find_window(5, [], lambda pid: ("x", 6 if pid == 5 else 5)))


class DispatchTest(unittest.TestCase):
    def test_lua_syntax_first(self):
        fake = FakeHyprctl()
        windows.focus(win(10, "0xa"), run=fake)
        self.assertEqual(fake.dispatches(), ['hl.dsp.focus({ window = "address:0xa" })'])

    def test_falls_back_to_classic_syntax(self):
        fake = FakeHyprctl(refuse=("hl.dsp",))
        windows.focus(win(10, "0xa"), run=fake)
        self.assertEqual(fake.dispatches(), ['hl.dsp.focus({ window = "address:0xa" })', "focuswindow address:0xa"])

    def test_both_failing_raises(self):
        with self.assertRaises(windows.WindowError):
            windows.focus(win(10, "0xa"), run=FakeHyprctl(refuse=("hl.dsp", "focuswindow")))

    def test_special_workspace_window_is_brought_here(self):
        fake = FakeHyprctl(active=4)
        windows.focus(win(10, "0xa", workspace="special:minimized"), run=fake)
        self.assertEqual(fake.dispatches(), [
            'hl.dsp.window.move({ window = "address:0xa", workspace = "4", follow = false })',
            'hl.dsp.focus({ window = "address:0xa" })',
        ])

    def test_missing_hyprctl(self):
        def missing(*a, **k):
            raise FileNotFoundError("hyprctl")
        with self.assertRaises(windows.WindowError):
            windows.clients(run=missing)


class PickSessionTest(unittest.TestCase):
    SESSIONS = [
        {"id": "aaa111", "status": "working", "updated": 5},
        {"id": "aab222", "status": "needs-input", "updated": 1},
        {"id": "ccc333", "status": "idle", "updated": 9},
    ]

    def test_default_is_the_one_waiting(self):
        self.assertEqual(pick_session(self.SESSIONS, None)["id"], "aab222")

    def test_prefix(self):
        self.assertEqual(pick_session(self.SESSIONS, "ccc")["id"], "ccc333")

    def test_ambiguous_and_missing(self):
        for wanted in ("aa", "zzz"):
            with self.assertRaises(windows.WindowError):
                pick_session(self.SESSIONS, wanted)
        with self.assertRaises(windows.WindowError):
            pick_session([], None)


class DismissCliTest(unittest.TestCase):
    def test_dismiss_by_prefix_sends_reason(self):
        import contextlib
        import io
        from unittest import mock
        from omaorchestra.__main__ import main
        sent = []

        def fake_request(payload, timeout=1.0):
            sent.append(payload)
            if payload["cmd"] == "list":
                return {"ok": True, "sessions": PickSessionTest.SESSIONS}
            return {"ok": True, "removed": True}

        out = io.StringIO()
        with mock.patch("omaorchestra.client.request", fake_request), contextlib.redirect_stdout(out):
            self.assertEqual(main(["dismiss", "ccc"]), 0)
        self.assertEqual(sent[-1], {"cmd": "remove", "session_id": "ccc333", "reason": "dismissed"})
        self.assertIn("dismissed ccc333", out.getvalue())

    def test_dismiss_needs_an_id(self):
        import contextlib
        import io
        from omaorchestra.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["dismiss"])


if __name__ == "__main__":
    unittest.main()
