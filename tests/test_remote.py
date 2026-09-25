import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __main__ as cli, config, daemon, remote
from omaorchestra.registry import Registry

LIMIT = {"agent": "claude", "name": "Claude Code", "label": "Session (5-hour)", "percent": 1.0, "resetsAt": None}


def s(status, since=1000, **extra):
    return {"id": "s1", "status": status, "status_since": since, "cwd": "/w/proj", **extra}


def remote_settings(**changes):
    return {**config.defaults()["remote"], "push": True, **changes}


class MessageTest(unittest.TestCase):
    def test_needs_you_at_each_level(self):
        session = s("needs-input", message="Allow Bash: rm -rf build?", title="Fix the login bug")
        self.assertEqual(remote.session_message("needs-you", None, session, "minimal"),
                         ("proj needs you", "Waiting for your input"))
        self.assertEqual(remote.session_message("needs-you", None, session, "summary"),
                         ("proj needs you", "Fix the login bug\nWaiting for your input"))
        self.assertEqual(remote.session_message("needs-you", None, session, "full"),
                         ("proj needs you", "Fix the login bug\nAllow Bash: rm -rf build?"))

    def test_minimal_never_carries_the_prompt_or_task(self):
        session = s("needs-input", message="SECRET prompt", title="SECRET title", task="SECRET task")
        for kind, previous in (("needs-you", None), ("finished", s("working", since=0))):
            title, body = remote.session_message(kind, previous, session, "minimal", now=500)
            self.assertNotIn("SECRET", title + body)

    def test_finished_and_failed(self):
        self.assertEqual(remote.session_message("finished", s("working", since=0), s("idle"), "minimal", now=3900),
                         ("proj finished", "Worked for 1h 5m"))
        item = {"cwd": "/w/proj", "task": "Refactor the parser\nwith care"}
        self.assertEqual(remote.failed_message(item, "claude is not installed", "minimal"),
                         ("proj: task failed to start", "A queued task could not start"))
        self.assertEqual(remote.failed_message(item, "claude is not installed", "full"),
                         ("proj: task failed to start",
                          "Refactor the parser\nA queued task could not start: claude is not installed"))

    def test_limits(self):
        self.assertEqual(remote.limit_message(LIMIT),
                         ("Claude Code reached its usage limit", "Claude Code's Session (5-hour) limit is at 100%"))
        self.assertEqual(remote.blocked_message({"kind": "budget", "text": "today's provider spend is $5.00"}),
                         ("Queue is waiting", "Today's provider spend is $5.00"))

    def test_new_topics_differ_and_fit(self):
        a, b = remote.new_topic(), remote.new_topic()
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^omaorchestra-[a-z0-9]{20}$")


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


class PostTest(unittest.TestCase):
    def test_json_to_the_server_root_with_the_token(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            return FakeResponse()

        secrets = {"topic": "omaorchestra-abc\n", "token": "tk_123\n"}
        remote.send(remote_settings(server="https://ntfy.example/"), "needs-you", "proj needs you", "Waiting",
                    lookup=secrets.get, opener=opener)
        (request,) = seen
        self.assertEqual(request.full_url, "https://ntfy.example/")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer tk_123")
        self.assertEqual(json.loads(request.data), {"topic": "omaorchestra-abc", "title": "proj needs you",
                                                    "message": "Waiting", "priority": 4, "tags": ["bell"]})

    def test_no_topic(self):
        with self.assertRaisesRegex(remote.RemoteError, "no topic"):
            remote.send(remote_settings(), "needs-you", "t", "b", lookup=lambda name: None)

    def test_errors_become_remote_errors(self):
        def refused(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        def offline(request, timeout):
            raise urllib.error.URLError("Name or service not known")

        with self.assertRaisesRegex(remote.RemoteError, "403: the token may not publish"):
            remote.post("https://ntfy.sh", {}, opener=refused)
        with self.assertRaisesRegex(remote.RemoteError, "could not reach https://ntfy.sh: Name or service"):
            remote.post("https://ntfy.sh", {}, opener=offline)


class PusherTest(unittest.TestCase):
    def run_pusher(self, script, settings=None, fail=False):
        sent = []

        def send(settings, kind, title, body):
            if fail:
                raise remote.RemoteError("offline")
            sent.append((kind, title, body))

        async def main():
            p = remote.Pusher(settings or remote_settings(), {"finished_after": 120}, send=send)
            script(p)
            await asyncio.gather(*p.tasks)
            return p

        p = asyncio.run(main())
        return sent, p

    def test_session_changes(self):
        def script(p):
            p.changed(s("working"), s("needs-input"))
            p.changed(s("needs-input"), s("working"))           # answered: nothing to push
            p.changed(s("working", since=0), s("idle"))          # long work finished
            p.changed(s("working", since=1150), s("idle"))       # short work: nothing
        with mock.patch("time.time", return_value=1200):
            sent, _ = self.run_pusher(script)
        self.assertEqual([k for k, *_ in sent], ["needs-you", "finished"])

    def test_off_or_not_chosen_sends_nothing(self):
        def script(p):
            p.changed(None, s("needs-input"))
            p.task_failed({"cwd": "/w/proj"}, "x")
            p.limit_reached(LIMIT)
        self.assertEqual(self.run_pusher(script, remote_settings(push=False))[0], [])
        sent, _ = self.run_pusher(script, remote_settings(events=["usage-limit"]))
        self.assertEqual([k for k, *_ in sent], ["usage-limit"])

    def test_failures_never_raise(self):
        def script(p):
            p.changed(None, s("needs-input"))
        with self.assertLogs("omaorchestra", "WARNING") as logs:
            _, p = self.run_pusher(script, fail=True)
        self.assertTrue(p.failing)
        self.assertIn("push failed", logs.output[0])

    def test_no_event_loop_is_harmless(self):
        remote.Pusher(remote_settings(), {"finished_after": 120}, send=None).changed(None, s("needs-input"))


class Recorder:
    def __init__(self):
        self.calls = []

    def changed(self, previous, session):
        self.calls.append(("changed", session and session["status"]))

    def task_failed(self, item, error):
        self.calls.append(("failed", error))

    def limit_reached(self, block):
        self.calls.append(("limit", block["agent"]))

    def queue_blocked(self, block):
        self.calls.append(("blocked", block["agent"]))


class DaemonPushTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.pusher = Recorder()
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), is_alive=lambda p, st: False,
                               pusher=self.pusher)
        self.d.spawn = lambda cmd, **kw: None
        self.limit = None
        self.d.usage_check = lambda agent, threshold: self.limit
        self.d.usage_refresh = lambda agent: None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def queue(self, **extra):
        item = {"task": "t", "cwd": self.tmp.name, "worktree": False, "agent_bin": "true", "path": "/usr/bin:/bin",
                **extra}
        return self.d.handle({"cmd": "queue-add", "item": item})

    def kinds(self, kind):
        return [c for c in self.pusher.calls if c[0] == kind]

    def test_session_changes_reach_the_pusher(self):
        self.d.handle({"cmd": "update", "session_id": "s1", "agent": "claude", "status": "needs-input"})
        self.assertEqual(self.kinds("changed"), [("changed", "needs-input")])

    def test_a_task_that_cannot_start(self):
        self.queue(agent_bin="/no/such/agent")
        ((_, error),) = self.kinds("failed")
        self.assertIn("not installed", error)

    def test_an_agent_that_never_shows_up(self):
        self.queue()
        timeout = self.d.registry.LAUNCH_TIMEOUT
        with mock.patch("time.time", return_value=time.time() + timeout + 1):
            self.d.prune()  # it never reported, and its time is up
        self.assertEqual(self.kinds("failed"), [("failed", "the agent did not start")])

    def test_queue_blocked_pushes_once(self):
        self.limit = dict(LIMIT, percent=0.93)
        self.queue()
        self.d.dispatch()
        self.limit = dict(LIMIT, percent=0.95)  # still blocked, only the figure moved
        self.d.dispatch()
        self.assertEqual(self.kinds("blocked"), [("blocked", "claude")])

    def test_usage_limit_pushes_once_per_episode(self):
        self.d.handle({"cmd": "update", "session_id": "s1", "agent": "claude", "status": "working"})
        self.limit = LIMIT
        self.d.check_limits()
        self.d.check_limits()
        self.assertEqual(self.kinds("limit"), [("limit", "claude")])
        self.limit = None
        self.d.check_limits()
        self.limit = LIMIT
        self.d.check_limits()
        self.assertEqual(len(self.kinds("limit")), 2)

    def test_idle_agents_at_their_limit_do_not_push(self):
        self.d.handle({"cmd": "update", "session_id": "s1", "agent": "claude", "status": "idle"})
        self.limit = LIMIT
        self.d.check_limits()
        self.assertEqual(self.kinds("limit"), [])

    def test_reload_updates_the_pusher(self):
        pusher = remote.Pusher(config.defaults()["remote"], config.defaults()["notifications"], send=None)
        self.d.pusher = pusher
        Path(os.environ["OMAORCHESTRA_CONFIG"]).write_text('[remote]\npush = true\ncontent = "summary"\n')
        self.d.handle({"cmd": "reload"})
        self.assertEqual((pusher.settings["push"], pusher.settings["content"]), (True, "summary"))


class ConfigTest(unittest.TestCase):
    def test_remote_validation(self):
        good = config.validate({"remote": {"push": True, "server": "http://box.tailnet.ts.net:8080",
                                           "content": "full", "events": ["needs-you"]}})
        self.assertEqual(good["remote"]["events"], ["needs-you"])
        for bad in ({"server": "ntfy.sh"}, {"content": "everything"}, {"events": ["always"]}):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.validate({"remote": bad})

    def test_events_show_as_choices(self):
        field = next(f for sec in config.describe(config.defaults()) for f in sec["fields"] if f["key"] == "events")
        self.assertEqual((field["kind"], field["options"]), ("choices", list(config.REMOTE_EVENTS)))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml"),
                                                "OMAORCHESTRA_SOCKET": os.path.join(self.tmp.name, "none.sock")})
        self.env.start()
        self.stored = {}
        patches = [mock.patch.object(cli.keys, "store_remote", lambda n, v: self.stored.__setitem__(n, v.strip())),
                   mock.patch.object(cli.keys, "lookup_remote", lambda n: self.stored.get(n)),
                   mock.patch.object(cli.keys, "clear_remote", lambda n: self.stored.pop(n, None))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_new_topic_is_stored_and_shown_once(self):
        code, out = self.run_cli("remote", "topic", "--new")
        self.assertEqual(code, 0)
        self.assertIn(self.stored["topic"], out)
        self.assertIn("server https://ntfy.sh", out)
        self.assertEqual(self.run_cli("remote", "topic", "--show")[1].strip(), self.stored["topic"])
        code, out = self.run_cli("remote")
        self.assertIn("topic    stored in the keyring", out)
        self.assertNotIn(self.stored["topic"], out, "status never prints the topic")

    def test_bad_topic(self):
        with mock.patch("sys.stdin", io.StringIO("has a space")), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(self.run_cli("remote", "topic", "--stdin")[0], 1)
        self.assertNotIn("topic", self.stored)

    def test_test_sends_through_the_configured_server(self):
        sent = []
        with mock.patch.object(cli.remote, "send", lambda settings, *a: sent.append((settings["server"], a))):
            code, out = self.run_cli("remote", "test")
        self.assertEqual(code, 0)
        self.assertEqual(sent[0][0], "https://ntfy.sh")
        self.assertIn("push is off", out)


if __name__ == "__main__":
    unittest.main()
