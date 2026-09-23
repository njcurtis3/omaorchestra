import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from omaorchestra import daemon, hooks, transcript
from omaorchestra.app import present
from omaorchestra.registry import Registry

try:
    from PySide6.QtCore import QEventLoop, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from qt_app import application
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def write_transcript(path, entries, pad=0):
    with open(path, "w") as f:
        if pad:
            f.write(json.dumps({"type": "user", "filler": "x" * pad}) + "\n")
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TranscriptTest(unittest.TestCase):
    def test_newest_model_and_branch_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.jsonl"
            write_transcript(p, [
                {"type": "assistant", "gitBranch": "old", "message": {"model": "claude-sonnet-5"}},
                {"type": "assistant", "gitBranch": "main", "message": {"model": "claude-opus-5-5"}},
                {"type": "assistant", "message": {"model": "<synthetic>"}},
                {"type": "user", "gitBranch": ""},
                {"type": "user", "gitBranch": "HEAD"},
                "not an object",
            ])
            with open(p, "a") as f:
                f.write("{broken json\n")
            self.assertEqual(transcript.info(p), {"model": "claude-opus-5-5", "branch": "main", "title": None})

    def test_reads_only_the_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.jsonl"
            write_transcript(p, [{"type": "assistant", "gitBranch": "b", "message": {"model": "claude-haiku-4-5"}}], pad=5000)
            self.assertEqual(transcript.info(p, tail_bytes=200)["model"], "claude-haiku-4-5")

    def test_missing_file(self):
        self.assertEqual(transcript.info("/nonexistent.jsonl"), {"model": None, "branch": None, "title": None})
        self.assertEqual(transcript.info(None), {"model": None, "branch": None, "title": None})

    def test_model_names(self):
        cases = {"claude-opus-5-5": "Opus 5.5", "claude-sonnet-5": "Sonnet 5",
                 "claude-haiku-4-5-20251001": "Haiku 4.5", "gpt-5": "Gpt 5", None: "", "": ""}
        for model, name in cases.items():
            self.assertEqual(transcript.model_name(model), name, model)


class DaemonTranscriptTest(unittest.TestCase):
    def test_reads_transcript_on_status_change_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.jsonl"
            write_transcript(t, [{"type": "assistant", "gitBranch": "main", "message": {"model": "claude-opus-5-5"}}])
            d = daemon.Daemon(Registry(Path(tmp) / "r.json"))
            reads = []
            real = transcript.info
            transcript.info = lambda path, *a: reads.append(path) or real(path, *a)
            try:
                up = {"cmd": "update", "session_id": "s", "agent": "claude", "transcript_path": str(t)}
                s = d.handle({**up, "status": "working"})["session"]
                d.handle({**up, "status": "working"})   # same status, model known: no read
                d.handle({**up, "status": "idle"})      # new status: read again
            finally:
                transcript.info = real
        self.assertEqual((s["model"], s["branch"], s["transcript_path"]), ("claude-opus-5-5", "main", str(t)))
        self.assertEqual(len(reads), 2)

    def test_reported_model_and_branch_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.jsonl"
            write_transcript(t, [{"type": "assistant", "gitBranch": "main", "message": {"model": "claude-opus-5-5"}}])
            d = daemon.Daemon(Registry(Path(tmp) / "r.json"))
            s = d.handle({"cmd": "update", "session_id": "s", "agent": "codex", "status": "working",
                          "transcript_path": str(t), "model": "gpt-5"})["session"]
            plain = d.handle({"cmd": "update", "session_id": "p", "agent": "codex", "status": "idle",
                              "model": "gpt-5", "branch": "dev"})["session"]
        self.assertEqual((s["model"], s["branch"]), ("gpt-5", "main"))
        self.assertEqual((plain["model"], plain["branch"]), ("gpt-5", "dev"))

    def test_hook_passes_the_transcript_path(self):
        req = hooks.request_for({"hook_event_name": "Stop", "session_id": "s", "transcript_path": "/t.jsonl"})
        self.assertEqual(req["transcript_path"], "/t.jsonl")


class PresentTest(unittest.TestCase):
    SESSIONS = [
        {"id": "i", "status": "idle", "status_since": 50, "cwd": "/w/idle"},
        {"id": "w1", "status": "working", "status_since": 10, "updated": 999, "cwd": "/w/a"},
        {"id": "w2", "status": "working", "status_since": 20, "updated": 11, "cwd": "/w/b", "branch": "feat", "model": "claude-opus-5-5"},
        {"id": "n", "status": "needs-input", "status_since": 5, "cwd": "/w/n/"},
    ]

    def test_order_is_waiting_first_then_by_status_since(self):
        self.assertEqual([r["id"] for r in present.ordered(self.SESSIONS)], ["n", "w2", "w1", "i"])

    def test_row_fields(self):
        r = present.row(self.SESSIONS[2])
        self.assertEqual((r["project"], r["statusLabel"], r["modelName"], r["since"]), ("b", "Working", "Opus 5.5", 20))
        self.assertEqual(present.row({"id": "x", "status": "needs-input", "cwd": "/w/n/"})["project"], "n")
        self.assertEqual(present.project(None), "(unknown)")

    def test_filtering(self):
        rows = present.ordered(self.SESSIONS)
        self.assertEqual([r["id"] for r in rows if present.matches(r, "working", "")], ["w2", "w1"])
        self.assertEqual([r["id"] for r in rows if present.matches(r, "", "FEAT")], ["w2"])
        self.assertEqual([r["id"] for r in rows if present.matches(r, "", "opus")], ["w2"])
        self.assertEqual([r["id"] for r in rows if present.matches(r, "idle", "feat")], [])

    def test_duration(self):
        self.assertEqual([present.duration(s) for s in (5, 125, 7300, 90000, -1)], ["5s", "2m", "2h 1m", "1d 1h", "0s"])


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class DashboardRenderTest(unittest.TestCase):
    """Render SessionsPage offscreen with sample sessions, in both layouts,
    and fail on any QML warning."""

    def test_renders_list_and_grid_without_warnings(self):
        from omaorchestra.app.backend import Sessions, Theme
        app = application()  # noqa: F841 (must exist while QML runs)
        warnings = []
        previous = qInstallMessageHandler(lambda mode, ctx, msg: warnings.append(msg))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                theme = Theme(Path(tmp) / "none" / "colors.toml")
                sessions = Sessions()
                sessions._on_snapshot([
                    {**s, "updated": 1, "started": 1, "agent": "claude", "message": "Allow Bash?" if s["id"] == "n" else None}
                    for s in PresentTest.SESSIONS
                ])
                engine = QQmlEngine()
                engine.rootContext().setContextProperty("theme", theme)
                engine.rootContext().setContextProperty("sessions", sessions)
                qml = ROOT / "src" / "omaorchestra" / "app" / "qml" / "SessionsPage.qml"
                component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml)))
                page = component.createWithInitialProperties({"width": 900, "height": 600})
                self.assertIsNotNone(page, component.errorString())
                loop = QEventLoop()
                for grid in (False, True):
                    page.setProperty("grid", grid)
                    QTimer.singleShot(100, loop.quit)
                    loop.exec()
                self.assertEqual(len(page.property("shown")), 4)
                page.setProperty("statusFilter", "needs-input")
                self.assertEqual([r["id"] for r in page.property("shown")], ["n"])
                page.deleteLater()
        finally:
            qInstallMessageHandler(previous)
        self.assertEqual(warnings, [])


class TimelineTest(unittest.TestCase):
    def test_durations_between_changes(self):
        items = present.timeline({"status": "idle", "history": [
            {"status": "working", "at": 100}, {"status": "needs-input", "at": 160}, {"status": "idle", "at": 200}]})
        self.assertEqual([(i["label"], i["at"], i["until"]) for i in items],
                         [("Working", 100, 160), ("Waiting", 160, 200), ("Idle", 200, None)])

    def test_session_without_history(self):
        self.assertEqual(present.timeline({"status": "working", "status_since": 50}),
                         [{"status": "working", "label": "Working", "at": 50, "until": None}])
        self.assertEqual(present.timeline({}), [])

    def test_clocks(self):
        self.assertRegex(present.clock(1790000000), r"^\d\d:\d\d:\d\d$")
        self.assertRegex(present.iso_clock("2026-09-23T21:17:03.511Z"), r"^\d\d:\d\d:\d\d$")
        self.assertEqual((present.clock(0), present.iso_clock(""), present.iso_clock("nope")), ("", "", ""))


@unittest.skipUnless(HAVE_QT, "PySide6 not available to this Python")
class DetailRenderTest(unittest.TestCase):
    def test_detail_tabs_render_without_warnings(self):
        from omaorchestra.app.backend import Sessions, Theme
        application()
        warnings = []
        previous = qInstallMessageHandler(lambda mode, ctx, msg: warnings.append(msg))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "proj"
                repo.mkdir()
                subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
                (repo / "a.txt").write_text("x\n")
                subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
                t = Path(tmp) / "t.jsonl"
                write_transcript(t, [
                    {"type": "user", "timestamp": "2026-09-23T21:17:03Z", "message": {"content": "do it"}},
                    {"type": "assistant", "timestamp": "2026-09-23T21:17:04Z",
                     "message": {"model": "claude-opus-5-5", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
                ])
                sessions = Sessions()
                sessions._on_snapshot([{
                    "id": "d1", "agent": "claude", "status": "needs-input", "cwd": str(repo), "started": 1,
                    "updated": 2, "status_since": 2, "message": "Allow Bash?", "title": "Doing it",
                    "transcript_path": str(t), "history": [{"status": "working", "at": 1}, {"status": "needs-input", "at": 2}],
                }])
                results = []
                sessions.changesReady.connect(lambda sid, r: results.append((sid, r)))
                engine = QQmlEngine()
                theme = Theme(Path(tmp) / "none" / "colors.toml")  # keep a reference: QML does not
                engine.rootContext().setContextProperty("theme", theme)
                engine.rootContext().setContextProperty("sessions", sessions)
                qml = ROOT / "src" / "omaorchestra" / "app" / "qml" / "SessionsPage.qml"
                component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml)))
                page = component.createWithInitialProperties({"width": 900, "height": 600, "selectedId": "d1"})
                self.assertIsNotNone(page, component.errorString())
                loop = QEventLoop()

                def spin(ms=100):
                    QTimer.singleShot(ms, loop.quit)
                    loop.exec()

                spin()
                self.assertEqual([i["text"] for i in sessions.activity("d1")], ["do it", "Bash: ls"])
                sessions.requestChanges("d1")
                for _ in range(50):
                    if results:
                        break
                    spin(50)
                self.assertTrue(results and results[0][1]["repo"])
                self.assertIn("+x", results[0][1]["diff"])
                # The session going away shows the "ended" state.
                sessions._on_event({"event": "removed", "id": "d1", "reason": "session-end"})
                spin()
                page.deleteLater()
                spin()
        finally:
            qInstallMessageHandler(previous)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
