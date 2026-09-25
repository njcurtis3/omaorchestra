import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __main__ as cli, adapters, approvals, claude_settings, client, config, daemon, permissions, top
from omaorchestra.registry import Registry

ROOT = Path(__file__).resolve().parent.parent
EVENT = {"hook_event_name": "PermissionRequest", "session_id": "s1", "cwd": "/w/site", "tool_name": "Bash",
         "tool_input": {"command": "npm test", "description": "Run the tests"}}


def remote(**changes):
    return {**config.defaults()["remote"], **changes}


class DescribeTest(unittest.TestCase):
    def test_what_the_agent_wants(self):
        self.assertEqual(approvals.describe("Bash", {"command": "npm  test\n--watch"}), "Bash: npm test --watch")
        self.assertEqual(approvals.describe("Edit", {"file_path": "/w/a.py", "old_string": "x"}), "Edit: /w/a.py")
        self.assertEqual(approvals.describe("WebFetch", {"url": "https://x.org", "prompt": "p"}),
                         "WebFetch: https://x.org")
        self.assertEqual(approvals.describe("mcp__github__create_issue", {"title": "t"}, "github"),
                         'github: create_issue: {"title": "t"}')
        self.assertEqual(approvals.describe(None, None), "a tool")
        self.assertLessEqual(len(approvals.describe("Bash", {"command": "x" * 1000})), approvals.SUMMARY_LIMIT)

    def test_where_from(self):
        self.assertEqual(approvals.where_from({"SSH_CONNECTION": "100.64.1.2 5555 100.70.1.1 22"}),
                         "over SSH from 100.64.1.2")
        self.assertEqual(approvals.where_from({}), "")


class HookAskTest(unittest.TestCase):
    def ask(self, answer, settings=None):
        sent = []

        def request(payload, timeout):
            sent.append((payload, timeout))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return approvals.ask(EVENT, settings or remote(), request), sent

    def test_allow_is_one_answer_not_a_rule(self):
        out, sent = self.ask({"ok": True, "decision": {"behavior": "allow", "updatedPermissions": [{"x": 1}]}})
        self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                      "decision": {"behavior": "allow"}}})
        payload, timeout = sent[0]
        self.assertEqual((payload["cmd"], payload["summary"], timeout), ("approval-ask", "Bash: npm test", 615))

    def test_deny_carries_a_message(self):
        out, _ = self.ask({"ok": True, "decision": {"behavior": "deny", "message": None}})
        self.assertEqual(out["hookSpecificOutput"]["decision"], {"behavior": "deny", "message": approvals.DENY_MESSAGE})

    def test_no_answer_leaves_it_to_the_terminal(self):
        for answer in ({"ok": True, "decision": None}, {"ok": False}, client.DaemonUnavailable("x"),
                       {"ok": True, "decision": {"behavior": "maybe"}}):
            self.assertIsNone(self.ask(answer)[0])
        out, sent = self.ask({"ok": True, "decision": {"behavior": "allow"}}, remote(answer_prompts=False))
        self.assertEqual((out, sent), (None, []))


class PendingTest(unittest.TestCase):
    def test_find_answer_settle(self):
        async def main():
            loop = asyncio.get_running_loop()
            p = approvals.Pending()
            a = p.add({"session_id": "s1", "summary": "Bash: ls"}, loop.create_future(), now=1)
            b = p.add({"session_id": "s2", "summary": "Edit: x"}, loop.create_future(), now=2)
            self.assertEqual([i["id"] for i in p.public()], [a["id"], b["id"]])
            self.assertNotIn("future", p.public()[0])
            p.answer(a["id"][:4], "allow", source="top")
            self.assertEqual(a["future"].result()["behavior"], "allow")
            with self.assertRaises(KeyError):
                p.answer(a["id"], "deny")  # already answered
            with self.assertRaises(KeyError):
                p.answer("zzzzzz", "allow")
            with self.assertRaises(KeyError):
                p.answer(b["id"], "always")
            p.settle_session("s2", "answered at the terminal")
            self.assertEqual(b["future"].result(), {"behavior": None, "reason": "answered at the terminal"})
        asyncio.run(main())


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        settings = config.defaults()
        settings["remote"]["answer_wait"] = 30
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        self.d.is_locked = lambda: False
        self.events = asyncio.Queue()
        self.d.subscribers.add(self.events)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def ask(self, then, away="on", wait=None):
        """Ask as the hook would, run `then(item)` once the request waits,
        and return the daemon's reply."""
        async def main():
            self.d.away.mode = away
            if wait:
                self.d.settings["remote"]["answer_wait"] = wait
            reader = asyncio.StreamReader()
            self.reader = reader
            task = asyncio.ensure_future(self.d.ask_approval(
                {"session_id": "s1", "tool": "Bash", "summary": "Bash: npm test", "cwd": "/w/site"}, reader))
            for _ in range(100):
                await asyncio.sleep(0.01)
                if self.d.approvals.items or task.done():
                    break
            if self.d.approvals.items:
                then(next(iter(self.d.approvals.items.values())))
            return await asyncio.wait_for(task, 5)
        return asyncio.run(main())

    def recorded(self):
        path = Path(self.tmp.name) / "approvals.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_at_the_desk_nothing_waits(self):
        reply = self.ask(lambda item: self.fail("nothing should wait"), away="off")
        self.assertEqual(reply, {"ok": True, "decision": None, "reason": "at the desk"})

    def test_answered_remotely(self):
        reply = self.ask(lambda item: self.assertTrue(self.d.handle(
            {"cmd": "approval-answer", "id": item["id"], "behavior": "allow", "source": "top"})["ok"]))
        self.assertEqual(reply["decision"]["behavior"], "allow")
        self.assertEqual(self.d.approvals.items, {})
        entry = self.recorded()[-1]
        self.assertEqual((entry["kind"], entry["outcome"], entry["source"], entry["summary"]),
                         ("remote", "allowed", "top", "Bash: npm test"))
        seen = []
        while not self.events.empty():
            seen.append(self.events.get_nowait())
        self.assertEqual([len(m["approvals"]) for m in seen if m["event"] == "approvals"], [1, 0])

    def test_denied_with_a_message(self):
        reply = self.ask(lambda item: self.d.handle({"cmd": "approval-answer", "id": item["id"], "behavior": "deny",
                                                     "message": "not now"}))
        self.assertEqual(reply["decision"], {"behavior": "deny", "message": "not now"})

    def test_answered_at_the_terminal(self):
        def terminal(item):
            before = {"id": "s1", "status": "needs-input"}
            self.d.changed(before, {"id": "s1", "status": "working"})
        reply = self.ask(terminal)
        self.assertEqual(reply, {"ok": True, "decision": None, "reason": "answered at the terminal"})
        self.assertEqual(self.recorded()[-1]["outcome"], "answered at the terminal")

    def test_still_waiting_is_not_settled(self):
        def notification_again(item):
            self.d.changed({"id": "s1", "status": "needs-input"}, {"id": "s1", "status": "needs-input"})
            self.assertFalse(item["future"].done())
            self.d.handle({"cmd": "approval-answer", "id": item["id"], "behavior": "allow"})
        self.assertEqual(self.ask(notification_again)["decision"]["behavior"], "allow")

    def test_the_agent_gives_up(self):
        reply = self.ask(lambda item: self.reader.feed_eof())
        self.assertEqual(reply["reason"], "the agent stopped waiting")

    def test_no_answer_in_time(self):
        reply = self.ask(lambda item: None, wait=0.1)
        self.assertEqual(reply["reason"], "no answer in time")

    def test_list_and_bad_answers(self):
        self.assertEqual(self.d.handle({"cmd": "approvals"}), {"ok": True, "approvals": []})
        self.assertFalse(self.d.handle({"cmd": "approval-answer", "id": "abc", "behavior": "allow"})["ok"])


class AgentAnswerTest(unittest.TestCase):
    def write(self, root, pid, comm, ppid, environ=b""):
        folder = Path(root) / str(pid)
        folder.mkdir()
        (folder / "stat").write_text(f"{pid} ({comm}) S {ppid} " + " ".join(["0"] * 30))
        (folder / "environ").write_bytes(environ)

    def test_under_agent(self):
        from omaorchestra import procs
        with tempfile.TemporaryDirectory() as root:
            self.write(root, 10, "claude", 1)
            self.write(root, 20, "bash", 10)
            self.write(root, 30, "omaorchestra", 20)
            self.write(root, 40, "bash", 1)
            self.write(root, 50, "python3", 40, b"PATH=/bin\0CLAUDECODE=1\0")
            self.write(root, 60, ".opencode", 1)
            self.write(root, 70, "sh", 60)
            names = {"claude", "codex", "opencode", ".opencode"}
            self.assertEqual(procs.under_agent(30, names, proc=root), "claude")
            self.assertIsNone(procs.under_agent(40, names, proc=root))
            self.assertEqual(procs.under_agent(50, names, proc=root), "claude")
            self.assertEqual(procs.under_agent(70, names, proc=root), "opencode")

    def test_the_daemon_refuses_an_agent(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": tmp}):
            d = daemon.Daemon(Registry(Path(tmp) / "sessions.json"))
            with mock.patch.object(daemon.procs, "under_agent", return_value="codex"):
                reply = d.answer_approval({"id": "abc", "behavior": "allow"}, peer=1234)
            self.assertFalse(reply["ok"])
            self.assertIn("inside an agent (codex)", reply["error"])


class RecordTest(unittest.TestCase):
    def test_remote_answers_show_where_they_came_from(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": tmp}):
            permissions.log({"kind": "asked", "session": "s1", "project": "/w/site", "message": "Allow Bash?"})
            permissions.log({"kind": "remote", "session": "s1", "outcome": "allowed",
                             "source": "top over SSH from 100.64.1.2"})
            permissions.log({"kind": "answered", "session": "s1", "outcome": "continued"})
            permissions.log({"kind": "asked", "session": "s2", "project": "/w/api"})
            permissions.log({"kind": "remote", "session": "s2", "outcome": "no answer in time"})
            permissions.log({"kind": "answered", "session": "s2", "outcome": "continued"})
            outcomes = {e["session"]: e["outcome"] for e in permissions.record()}
        self.assertEqual(outcomes, {"s1": "allowed from top over SSH from 100.64.1.2", "s2": "continued"})


class ClaudeHookTest(unittest.TestCase):
    def test_reports_waiting_with_what_it_asks(self):
        request = adapters.get("claude").request_for(EVENT)
        self.assertEqual((request["status"], request["message"]), ("needs-input", "Allow Bash: npm test?"))

    def test_the_permission_hook_gets_a_long_timeout(self):
        settings = claude_settings.install({}, "/usr/bin/omaorchestra hook claude", adapters.get("claude").events,
                                           adapters.get("claude").hook_timeouts)
        timeouts = {event: groups[0]["hooks"][0]["timeout"] for event, groups in settings["hooks"].items()}
        self.assertEqual(timeouts["PermissionRequest"], approvals.HOOK_TIMEOUT)
        self.assertEqual(timeouts["Stop"], claude_settings.HOOK_TIMEOUT)
        self.assertFalse(adapters.get("codex").answers_permissions)


class CliTest(unittest.TestCase):
    def run_cli(self, argv, answer):
        sent = []

        def request(payload, timeout=1.0):
            sent.append(payload)
            return answer
        out = io.StringIO()
        with mock.patch.object(cli.client, "request", request), redirect_stdout(out), \
                mock.patch("sys.stderr", io.StringIO()), mock.patch.dict(os.environ, {"SSH_CONNECTION": ""}):
            code = cli.main(argv)
        return code, out.getvalue(), sent

    def test_list(self):
        item = {"id": "a1b2c3", "session_id": "s1", "tool": "Bash", "summary": "Bash: npm test", "cwd": "/w/site",
                "asked": time.time() - 30}
        code, out, _ = self.run_cli(["approvals"], {"ok": True, "approvals": [item]})
        self.assertIn("a1b2c3  site", out)
        self.assertIn("Bash: npm test", out)
        self.assertIn("away", self.run_cli(["approvals"], {"ok": True, "approvals": []})[1])

    def test_approve_and_deny(self):
        item = {"id": "a1b2c3", "summary": "Bash: npm test", "cwd": "/w/site"}
        code, out, sent = self.run_cli(["approve", "a1b"], {"ok": True, "approval": item})
        self.assertEqual((code, sent[0]["behavior"], sent[0]["id"], sent[0]["source"]),
                         (0, "allow", "a1b", "the command line"))
        self.assertIn("allowed: Bash: npm test (site)", out)
        _, _, sent = self.run_cli(["deny", "a1b", "--message", "use yarn"], {"ok": True, "approval": item})
        self.assertEqual((sent[0]["behavior"], sent[0]["message"]), ("deny", "use yarn"))
        code, _, _ = self.run_cli(["approve", "zz"], {"ok": False, "error": "no request zz is waiting"})
        self.assertEqual(code, 1)


class TopTest(unittest.TestCase):
    def test_allow_after_a_yes_and_deny_at_once(self):
        sent = []

        def request(payload, timeout=1.0):
            sent.append(payload)
            return {"ok": True}
        t = top.Top(request=request, now=lambda: 1000, agents=["claude"])
        t.apply({"sessions": [{"id": "s1", "agent": "claude", "status": "needs-input", "cwd": "/w/site",
                               "status_since": 900, "message": "Claude needs your permission"}],
                 "approvals": [{"id": "a1b2c3", "session_id": "s1", "summary": "Bash: npm test", "cwd": "/w/site",
                                "tool": "Bash", "asked": 990}]})
        text = t.render(40, 20).text()
        self.assertIn("? Bash: npm test", text)
        self.assertIn(" y Allow… ", text)
        t.key("y")
        self.assertTrue(t.detail)
        self.assertIn("asks: Bash: npm test", t.render(40, 20).text())
        self.assertIn("Allow this, just this once?", t.render(40, 20).text())
        self.assertEqual(sent, [])
        t.key("y")
        self.assertEqual((sent[-1]["cmd"], sent[-1]["id"], sent[-1]["behavior"]), ("approval-answer", "a1b2c3", "allow"))
        self.assertTrue(sent[-1]["source"].startswith("top"))
        t.apply({"event": "approvals", "approvals": [{"id": "d4e5f6", "session_id": "s1", "summary": "Bash: rm x",
                                                       "cwd": "/w/site", "tool": "Bash", "asked": 995}]})
        t.key("x")
        self.assertEqual((sent[-1]["id"], sent[-1]["behavior"]), ("d4e5f6", "deny"))


class EndToEndTest(unittest.TestCase):
    """The real hook command, against a real daemon, answered by `approve`."""

    def test_hook_waits_for_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "OMAORCHESTRA_SOCKET": os.path.join(tmp, "o.sock"),
                   "OMAORCHESTRA_STATE_DIR": os.path.join(tmp, "state"),
                   "OMAORCHESTRA_CONFIG": os.path.join(tmp, "c.toml"),
                   "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + os.path.join(tmp, "no-bus")}
            for name in ("WAYLAND_DISPLAY", "OMARCHY_PATH", "JOURNAL_STREAM", "SSH_CONNECTION", "SSH_CLIENT"):
                env.pop(name, None)
            Path(env["OMAORCHESTRA_CONFIG"]).write_text("[notifications]\nwaiting = false\nfinished_after = 0\n")
            omaorchestra = str(ROOT / "bin" / "omaorchestra")
            log = open(os.path.join(tmp, "daemon.log"), "w+")
            d = subprocess.Popen([omaorchestra, "daemon"], env=env, stderr=log)
            try:
                deadline = time.time() + 10
                while not Path(env["OMAORCHESTRA_SOCKET"]).exists() and time.time() < deadline:
                    time.sleep(0.05)
                run = lambda *a: subprocess.run([omaorchestra, *a], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(run("away", "on").returncode, 0)
                hook = subprocess.Popen([omaorchestra, "hook", "claude"], env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True)
                hook.stdin.write(json.dumps(EVENT))
                hook.stdin.close()
                listed = ""
                while "Bash: npm test" not in listed and time.time() < deadline:
                    listed = run("approvals").stdout
                    time.sleep(0.1)
                self.assertIn("Bash: npm test", listed)
                self.assertIsNone(hook.poll(), "the hook waits for an answer")
                rid = listed.split()[0]
                # From inside an agent (as this test may run), the answer is refused.
                inside = subprocess.run([omaorchestra, "approve", rid], env={**env, "CLAUDECODE": "1"},
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(inside.returncode, 1)
                self.assertIn("inside an agent", inside.stderr)
                # From a process outside any agent (detached, so no agent is its
                # ancestor): allowed.
                clean = {k: v for k, v in env.items() if k != "CLAUDECODE"}
                result = os.path.join(tmp, "approve.out")
                subprocess.run(["sh", "-c", f'(sleep 0.3; exec "$0" approve {rid} > "$1" 2>&1) &',
                                omaorchestra, result], env=clean, timeout=10)
                answered = ""
                while "allowed" not in answered and "omaorchestra:" not in answered and time.time() < deadline + 10:
                    time.sleep(0.1)
                    answered = open(result).read() if os.path.exists(result) else ""
                self.assertIn("allowed: Bash: npm test", answered)
                out, _ = hook.communicate(timeout=10)
                self.assertEqual(json.loads(out), {"hookSpecificOutput": {
                    "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})
                self.assertEqual(hook.returncode, 0)
                # At the desk, the hook returns at once and says nothing.
                run("away", "off")
                quick = subprocess.run([omaorchestra, "hook", "claude"], env=env, input=json.dumps(EVENT),
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(quick.stdout, "")
            finally:
                d.terminate()
                d.wait(timeout=5)
                log.close()


if __name__ == "__main__":
    unittest.main()
