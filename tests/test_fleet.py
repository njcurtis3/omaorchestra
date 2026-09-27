import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import client, daemon, fleet, fleet_reply, transcript
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry


def block(value, before="Here is what I found.", after=""):
    return f"{before}\n\n```json\n{json.dumps(value, indent=2)}\n```\n{after}"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


class ReplyTest(unittest.TestCase):
    def test_every_example_is_a_valid_reply(self):
        # The examples are what each node is shown, so they must pass.
        for role in fleet_reply.CONTRACTS:
            with self.subTest(role=role):
                result = fleet_reply.parse(role, block(fleet_reply.EXAMPLES[role]))
                self.assertIsInstance(result, dict)
                text = fleet_reply.reply_format(role)
                self.assertIn("```json", text)
                self.assertIn(fleet_reply.NOTES[role], text)

    def test_reading_the_block(self):
        for text, error in (("", "could not be read"), ("All done!", "does not end with a ```json block"),
                            ("```json\n{nope}\n```", "not valid JSON"), ("```json\n[1, 2]\n```", "JSON object")):
            with self.assertRaises(fleet_reply.ReplyError) as caught:
                fleet_reply.extract(text)
            self.assertIn(error, str(caught.exception))
        # The last block counts; an earlier one (a code sample) does not.
        text = block({"status": "blocked"}) + "\n" + block(example("other"), before="Final:")
        self.assertEqual(fleet_reply.extract(text)["status"], "done")
        self.assertEqual(fleet_reply.extract("```JSON\n{\"a\": 1}\n```")["a"], 1)

    def test_shape_errors_name_the_field(self):
        for role, reply, error in (
                ("scout", example("scout", build="yellow"), "build must be one of"),
                ("scout", {k: v for k, v in example("scout").items() if k != "facts"}, "no facts"),
                ("scout", example("scout", facts=[{"fact": "x"}]), "facts[0] has no where"),
                ("architect", example("architect", slices=[]), "slices needs at least 1"),
                ("architect", example("architect", slices=[{**example("architect")["slices"][0], "id": "Slice 1"}]),
                 "not a valid id"),
                ("builder", example("builder", changed="a.py"), "changed must be a list"),
                ("builder", example("builder", done_when={"command": "x", "output": "", "passed": "yes"}),
                 "done_when.passed must be true or false"),
                ("reviewer", example("reviewer", reran=""), "reran is empty")):
            with self.subTest(error=error), self.assertRaises(fleet_reply.ReplyError) as caught:
                fleet_reply.parse(role, block(reply))
            self.assertIn(error, str(caught.exception))

    def test_keeps_only_the_contracts_fields_and_cuts_long_output(self):
        reply = example("builder", extra="dropped")
        reply["done_when"] = {**reply["done_when"], "output": "x" * 20000}
        result = fleet_reply.parse("builder", block(reply))
        self.assertNotIn("extra", result)
        self.assertEqual(len(result["done_when"]["output"]), fleet_reply.OUTPUT_LIMIT)
        self.assertTrue(result["done_when"]["output"].endswith("…"))
        self.assertIsNone(fleet_reply.parse("scout", block(example("scout", plan_killer="")))["plan_killer"])
        self.assertEqual(fleet_reply.parse("architect", block(example("architect", edges=None)))["edges"], [])

    def test_contradictions_are_refused(self):
        slice_ = example("architect")["slices"][0]
        two = [slice_, {**slice_, "id": "s2", "files": ["b.py"]}]
        for role, reply, error in (
                ("builder", example("builder", done_when={"command": "t", "output": "1 failed", "passed": False}),
                 "done_when did not pass"),
                ("builder", example("builder", status="blocked", blocked=None,
                                    done_when={"command": "t", "output": "", "passed": False}), "does not say why"),
                ("reviewer", example("reviewer", findings=[]), "REJECT needs at least one blocker"),
                ("reviewer", example("reviewer", verdict="PASS"), "PASS with a blocker"),
                ("integrator", example("integrator", suite={"command": "t", "output": "", "passed": False}),
                 "which merge broke it"),
                ("architect", example("architect", slices=[slice_, slice_]), "share an id"),
                ("architect", example("architect", slices=[{**slice_, "files": ["/etc/passwd"]}]), "inside the repository"),
                ("architect", example("architect", slices=[{**slice_, "files": ["../x"]}]), "inside the repository"),
                ("architect", example("architect", slices=two, edges=[{"from": "s1", "to": "s9", "artifact": "a"}]),
                 "must join two different slices"),
                ("architect", example("architect", slices=two, edges=[{"from": "s1", "to": "s2", "artifact": "a"},
                                                                     {"from": "s2", "to": "s1", "artifact": "b"}]),
                 "round in a circle"),
                ("ops", {"status": "blocked", "summary": "no"}, "does not say why")):
            with self.subTest(error=error), self.assertRaises(fleet_reply.ReplyError) as caught:
                fleet_reply.parse(role, block(reply))
            self.assertIn(error, str(caught.exception))
        # Blocked with a reason, and a PASS with notes, are fine.
        fleet_reply.parse("builder", block(example("builder", status="blocked", blocked="needs b.py",
                                                   done_when={"command": "t", "output": "", "passed": False})))
        fleet_reply.parse("reviewer", block(example("reviewer", verdict="PASS", findings=[
            {"severity": "note", "where": "a.py:1", "what": "a name could be clearer"}])))

    def test_another_role_uses_the_general_contract(self):
        self.assertEqual(fleet_reply.contract_for("ops"), "other")
        self.assertIn('"summary"', fleet_reply.reply_format("ops"))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "work"
        self.folder.mkdir()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_create_save_load(self):
        state = fleet.create("  Add   pagination to the search page!  ", self.folder, now=1790000000)
        self.assertEqual(state["id"], "2026-09-21-add-pagination-to-the-search-page")
        self.assertEqual((state["goal"], state["status"], state["folder"]),
                         ("Add pagination to the search page!", "running", str(self.folder.resolve())))
        run_dir = fleet.runs_dir() / state["id"]
        self.assertEqual(run_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(fleet.load(state["id"]), state)
        self.assertEqual([e["event"] for e in fleet.read_activity(state["id"])], ["created"])
        second = fleet.create("Add pagination to the search page", self.folder, now=1790000000)
        self.assertEqual(second["id"], state["id"] + "-2")
        self.assertFalse(list(run_dir.glob("*.tmp")))

    def test_refusals(self):
        with self.assertRaises(fleet.RunError):
            fleet.create("   ", self.folder)
        with self.assertRaises(fleet.RunError):
            fleet.create("x", self.folder / "missing")
        with self.assertRaises(fleet.RunError):
            fleet.load("nothing-here")
        for bad in ("../escape", "", "a/b"):
            with self.assertRaises(fleet.RunError):
                fleet.load(bad)
        state = fleet.create("x", self.folder)
        (fleet.runs_dir() / state["id"] / "state.json").write_text('{"v": 99}')
        with self.assertRaises(fleet.RunError) as caught:
            fleet.load(state["id"])
        self.assertIn("format", str(caught.exception))

    def test_runs_and_find(self):
        old = fleet.create("first goal", self.folder, now=1000)
        new = fleet.create("second goal", self.folder, now=2000)
        (fleet.runs_dir() / "broken").mkdir()
        (fleet.runs_dir() / "broken" / "state.json").write_text("not json")
        self.assertEqual([s["id"] for s in fleet.runs()], [new["id"], old["id"]])
        self.assertEqual(fleet.find(new["id"][:11] + "second")["id"], new["id"])
        with self.assertRaises(fleet.RunError) as caught:
            fleet.find("19")
        self.assertIn("matches 2 runs", str(caught.exception))
        with self.assertRaises(fleet.RunError):
            fleet.find("nope")


class NodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state")})
        self.env.start()
        self.state = fleet.create("Look users up by email", self.tmp.name)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def events(self):
        return [e["event"] for e in fleet.read_activity(self.state["id"])]

    def test_attempts(self):
        self.assertEqual(fleet.add_node(self.state, "scout")["id"], "scout")
        self.assertEqual(fleet.add_node(self.state, "builder", "s1")["id"], "builder.s1")
        second = fleet.add_node(self.state, "builder", "s1", feedback=["try the index"])
        self.assertEqual((second["id"], second["attempt"], second["feedback"]), ("builder.s1.2", 2, ["try the index"]))
        self.assertEqual(fleet.latest(self.state, "builder", "s1")["id"], "builder.s1.2")
        self.assertIsNone(fleet.latest(self.state, "builder", "s1", done=True))
        with self.assertRaises(fleet.RunError):
            fleet.node(self.state, "nobody")

    def test_a_good_reply_is_stored_under_the_node(self):
        fleet.add_node(self.state, "scout")
        fleet.node_started(self.state, "scout", "S1", "claude")
        n = fleet.record_reply(self.state, "scout", block(example("scout")), git={"branch": "main", "head": "abc"})
        self.assertEqual((n["status"], n["written_by"], n["result"]["build"]), ("done", "scout", "red"))
        self.assertEqual(n["git"]["branch"], "main")
        self.assertEqual(self.state["status"], "running")
        self.assertEqual(fleet.for_session(self.state, "S1")["id"], "scout")
        self.assertEqual(self.events(), ["created", "started", "result"])

    def test_a_bad_reply_holds_until_it_is_fixed(self):
        fleet.add_node(self.state, "scout")
        fleet.node_started(self.state, "scout", "S1", "claude")
        n = fleet.record_reply(self.state, "scout", "I looked around; all good.")
        self.assertEqual(n["status"], "held")
        self.assertIn("does not end with a ```json block", n["error"])
        self.assertEqual((self.state["status"], self.state["held_by"]), ("held", "scout"))
        self.assertIn("scout: its reply", self.state["reason"])
        fleet.record_reply(self.state, "scout", block(example("scout")))
        self.assertEqual((self.state["status"], self.state["reason"], n["status"]), ("running", None, "done"))
        self.assertEqual(self.events()[-4:], ["bad-reply", "held", "result", "released"])

    def test_a_failed_session_holds_the_run(self):
        fleet.add_node(self.state, "builder", "s1")
        fleet.node_failed(self.state, "builder.s1", "stopped")
        self.assertEqual(self.state["nodes"]["builder.s1"]["status"], "failed")
        self.assertEqual(self.state["reason"], "builder.s1: its session stopped")

    def plan(self):
        """A scout and an architect that finished, with two slices."""
        fleet.add_node(self.state, "scout")
        fleet.record_reply(self.state, "scout", block(example("scout")))
        fleet.add_node(self.state, "architect")
        slice_ = example("architect")["slices"][0]
        fleet.record_reply(self.state, "architect", block(example("architect", slices=[
            slice_, {**slice_, "id": "s2", "intent": "Add an index", "files": ["db/schema.sql"]}])))

    def test_briefs_carry_what_came_before(self):
        fleet.add_node(self.state, "scout")
        scout = fleet.brief(self.state, "scout")
        self.assertIn("Look users up by email", scout)
        self.assertIn("first step", scout)
        self.plan()
        fleet.add_node(self.state, "architect", feedback=["Keep the old lookup as a fallback"])
        architect = fleet.brief(self.state, "architect.2")
        self.assertIn("src/auth/session.py:42", architect)  # the scout's facts
        self.assertIn("Plan-killer", architect)
        self.assertIn("## Your plan before", architect)
        self.assertIn("Keep the old lookup as a fallback", architect)

        fleet.add_node(self.state, "builder", "s1")
        builder = fleet.brief(self.state, "builder.s1")
        self.assertIn("Slice s1: Look users up by email", builder)
        self.assertIn("- s2: Add an index (db/schema.sql)", builder)  # the other slice, not its own
        self.assertNotIn("sent back", builder)
        fleet.record_reply(self.state, "builder.s1", block(example("builder")),
                           git={"branch": "fleet/s1", "head": "a" * 40, "base": "b" * 40})

        fleet.add_node(self.state, "reviewer", "s1")
        reviewer = fleet.brief(self.state, "reviewer.s1")
        self.assertIn("## The slice under review", reviewer)
        self.assertIn("4 passed in 0.31s", reviewer)
        self.assertIn("branch fleet/s1 (at aaaaaaaaaa, from bbbbbbbbbb)", reviewer)
        fleet.record_reply(self.state, "reviewer.s1", block(example("reviewer")))

        fleet.add_node(self.state, "builder", "s1")
        again = fleet.brief(self.state, "builder.s1.2")
        self.assertIn("attempt 2", again)
        self.assertIn("## It was sent back", again)
        self.assertIn("the lookup is case-sensitive (missed by the builder)", again)
        fleet.add_node(self.state, "reviewer", "s1")
        self.assertIn("## Earlier reviews of this slice", fleet.brief(self.state, "reviewer.s1.2"))

        fleet.add_node(self.state, "integrator")
        self.state["branch"] = "fleet/look-users-up"
        integrator = fleet.brief(self.state, "integrator")
        self.assertIn("- s1: Look users up by email; branch fleet/s1; latest review: REJECT", integrator)
        self.assertIn("- s2: Add an index; branch (none); latest review: not reviewed", integrator)
        self.assertIn("into branch fleet/look-users-up", integrator)

        task = fleet.task_for(self.state, "integrator")
        self.assertTrue(task.startswith(integrator))
        self.assertIn(fleet_reply.NOTES["integrator"], task)

    def test_a_builder_for_a_slice_the_plan_lacks(self):
        self.plan()
        fleet.add_node(self.state, "builder", "s9")
        with self.assertRaises(fleet.RunError):
            fleet.brief(self.state, "builder.s9")

    def test_another_role_sees_what_was_done(self):
        self.plan()
        fleet.add_node(self.state, "ops")
        self.assertIn("- scout: ", fleet.brief(self.state, "ops"))


class ReadReplyTest(unittest.TestCase):
    def test_claude_and_codex_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / "claude.jsonl"
            claude.write_text("\n".join(json.dumps(e) for e in (
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "the last reply"}]}},
            )) + "\n")
            self.assertEqual(fleet.read_reply({"agent": "claude", "transcript_path": str(claude)}), "the last reply")
            codex = Path(tmp) / "rollout.jsonl"
            codex.write_text("\n".join(json.dumps(e) for e in (
                {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                                      "content": [{"type": "input_text", "text": "instructions"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                      "content": [{"type": "output_text", "text": "codex says"}]}},
                {"type": "event_msg", "payload": {"type": "task_complete"}},
            )) + "\n")
            self.assertEqual(transcript.last_reply(str(codex)), "codex says")
            self.assertEqual(fleet.read_reply({"agent": "claude"}), "")

    def test_opencode_export(self):
        exported = {"info": {"id": "ses_1"}, "messages": [
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": "do it"}]},
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "opencode says"},
                                                      {"type": "tool", "tool": "read"}]},
            {"info": {"role": "assistant"}, "parts": [{"type": "tool", "tool": "bash"}]}]}
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "Exporting session...\n" + json.dumps(exported), "")
        self.assertEqual(fleet.opencode_reply("ses_1", run=run), "opencode says")
        self.assertEqual(calls[0], ["opencode", "export", "ses_1"])
        self.assertEqual(fleet.opencode_reply("ses_1", run=lambda c, **k: subprocess.CompletedProcess(c, 1, "", "")),
                         "")
        self.assertEqual(fleet.opencode_reply(None), "")


class DaemonFleetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml")})
        self.env.start()
        self.d = daemon.Daemon(Registry(self.root / "state" / "sessions.json"), is_alive=lambda p, s: True)
        self.published = []
        self.d.publish = self.published.append
        self.d.dispatch = lambda: None
        self.state = fleet.create("Look users up by email", self.tmp.name)
        fleet.add_node(self.state, "scout")
        fleet.node_started(self.state, "scout", "S1", "claude")
        fleet.save(self.state)
        self.transcript = self.root / "t.jsonl"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def reply(self, text):
        with open(self.transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")

    def update(self, status, sid="S1", **extra):
        self.d.handle({"cmd": "update", "session_id": sid, "agent": "claude", "status": status,
                       "cwd": self.tmp.name, "transcript_path": str(self.transcript), **extra})

    def fleet_event(self):
        return next(p for p in reversed(self.published) if p["event"] == "fleet")

    def node(self):
        return fleet.load(self.state["id"])["nodes"]["scout"]

    def test_the_reply_is_read_when_the_node_goes_idle(self):
        self.update("working", fleet=self.state["id"], node="scout")
        self.assertEqual(self.d.registry.sessions["S1"]["node"], "scout")
        self.update("working")  # later reports carry no fleet fields; the session keeps them
        self.reply("Still looking.")
        self.update("needs-input")
        self.update("working")
        self.reply(block(example("scout")))
        self.update("idle")
        n = self.node()
        self.assertEqual((n["status"], n["written_by"]), ("done", "scout"))
        events = [e["event"] for e in fleet.read_activity(self.state["id"])]
        self.assertEqual(events, ["created", "started", "working", "needs-input", "working", "idle", "result"])
        self.assertEqual(self.fleet_event()["status"], "running")
        # Going idle again after the node is done changes nothing.
        self.update("working")
        self.reply("Something else entirely.")
        self.update("idle")
        self.assertEqual(self.node()["result"]["build"], "red")

    def test_a_bad_reply_holds_and_a_fixed_one_releases(self):
        self.update("working", fleet=self.state["id"], node="scout")
        self.reply("Done looking around!")
        self.update("idle")
        state = fleet.load(self.state["id"])
        self.assertEqual((state["status"], state["nodes"]["scout"]["status"]), ("held", "held"))
        self.assertIn("```json", self.fleet_event()["reason"])
        # You ask it to end with the block; it does.
        self.update("working")
        self.reply(block(example("scout")))
        self.update("idle")
        state = fleet.load(self.state["id"])
        self.assertEqual((state["status"], state["nodes"]["scout"]["status"]), ("running", "done"))

    def test_a_session_that_ends_unfinished_fails_the_node(self):
        self.update("working", fleet=self.state["id"], node="scout")
        self.d.handle({"cmd": "remove", "session_id": "S1"})  # the agent quit mid-work
        state = fleet.load(self.state["id"])
        self.assertEqual(state["nodes"]["scout"]["status"], "failed")
        self.assertEqual(state["reason"], "scout: its session stopped")
        self.assertEqual([e["event"] for e in fleet.read_activity(self.state["id"])][-3:], ["ended", "failed", "held"])

    def test_another_sessions_node_is_left_alone(self):
        self.update("working", sid="S2", fleet=self.state["id"], node="scout")  # not the session that runs it
        self.reply("nothing useful")
        self.update("idle", sid="S2")
        self.assertEqual(self.node()["status"], "running")


class WatchTest(unittest.TestCase):
    def test_every_event_kind(self):
        stream = iter([{"sessions": []},
                       {"event": "away", "away": {"on": True}},
                       {"event": "approvals", "approvals": []},
                       {"event": "fleet", "run": "2026-09-27-x", "status": "held", "reason": "scout: its reply"},
                       {"event": "removed", "id": "abcdef123", "reason": "ended"}])
        out = io.StringIO()
        with mock.patch.object(client, "subscribe", lambda: stream), contextlib.redirect_stdout(out):
            self.assertEqual(main(["watch"]), 0)
        self.assertIn("fleet 2026-09-27-x: held (scout: its reply)", out.getvalue())
        self.assertIn("abcdef12  ended (ended)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
