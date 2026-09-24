import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import costs, daemon, providers, spend
from omaorchestra.registry import Registry


def entry(msg_id, model, inp=0, out=0, cw=0, cr=0, **extra):
    return {"type": "assistant", "message": {"id": msg_id, "model": model, "usage": {
        "input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}},
        **extra}


class CostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_PROVIDERS": os.path.join(self.tmp.name, "p.json"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.t = Path(self.tmp.name) / "t.jsonl"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, entries):
        self.t.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def test_each_message_counted_once(self):
        self.write([entry("m1", "claude-sonnet-5", inp=10, out=100), entry("m1", "claude-sonnet-5", inp=10, out=100),
                    entry("m2", "claude-sonnet-5", out=50, cw=1000, cr=5000), entry("m3", "<synthetic>", out=999),
                    {"type": "user", "message": {"content": "hi"}}])
        u = costs.transcript_usage(str(self.t))
        self.assertEqual(u, {"claude-sonnet-5": {"input": 10, "output": 150, "cache_write": 1000, "cache_read": 5000}})

    def test_prices_with_cache_multipliers(self):
        # Sonnet 5 at $2 in / $10 out: 1M in = $2, 1M out = $10, 1M write = $2.50, 1M read = $0.20
        usage = {"claude-sonnet-5": {"input": 1_000_000, "output": 1_000_000, "cache_write": 1_000_000,
                                     "cache_read": 1_000_000}, "mystery-model": {"input": 5, "output": 5,
                                                                                "cache_write": 0, "cache_read": 0}}
        c = costs.cost(usage)
        self.assertAlmostEqual(c["usd"], 2 + 10 + 2.5 + 0.2)
        self.assertEqual(c["unpriced"], ["mystery-model"])

    def test_routed_sessions_use_the_providers_prices(self):
        (Path(self.tmp.name) / "catalog.json").write_text(json.dumps({"openrouter": {"models": [
            {"id": "anthropic/claude-sonnet-5", "input_price": 3.0, "output_price": 15.0}]}}))
        self.write([entry("m1", "anthropic/claude-sonnet-5", out=1_000_000)])
        c = costs.session_cost({"transcript_path": str(self.t), "provider": "openrouter"})
        self.assertEqual((c["usd"], c["real"]), (15.0, True))
        self.assertFalse(costs.session_cost({"transcript_path": str(self.t)})["real"])

    def test_ledger(self):
        costs.record("openrouter", 1.25)
        costs.record("openrouter", 0.75)
        costs.record("anthropic", 2.0)
        costs.record("anthropic", -5)  # ignored
        self.assertEqual(costs.spent_today(), 4.0)
        for i in range(100):
            costs.record("x", 1, day=f"2020-01-{i:03d}")
        self.assertLessEqual(len(costs.ledger()), 92)

    def test_daemon_records_spend_as_a_routed_session_works(self):
        d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"))
        up = {"cmd": "update", "session_id": "s", "agent": "claude", "provider": "anthropic",
              "transcript_path": str(self.t)}
        self.write([entry("m1", "claude-sonnet-5", out=100_000)])        # $1.00
        d.handle({**up, "status": "working"})
        self.write([entry("m1", "claude-sonnet-5", out=100_000), entry("m2", "claude-sonnet-5", out=100_000)])
        d.handle({**up, "status": "working"})                               # same status: not yet
        d.handle({**up, "status": "idle"})                                  # $2.00 in all
        self.assertAlmostEqual(costs.spent_today(), 2.0)
        self.assertAlmostEqual(d.registry.sessions["s"]["cost"], 2.0)


class HoldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": self.tmp.name,
                                                "OMAORCHESTRA_PROVIDERS": os.path.join(self.tmp.name, "p.json"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        providers.add("anthropic")
        self.keys = mock.patch("omaorchestra.keys.lookup", lambda pid: "sk-test")
        self.keys.start()
        settings = daemon.config.defaults()
        settings["tasks"]["daily_budget"] = 5
        settings["tasks"]["max_parallel"] = 10
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        self.limit = None
        self.d.usage_check = lambda agent, threshold: self.limit
        self.d.usage_refresh = lambda agent: None
        self.started = []
        self.d.spawn = lambda cmd, **kw: self.started.append(cmd[-1])
        self.d.handle({"cmd": "queue-hold"})

    def tearDown(self):
        self.keys.stop()
        self.env.stop()
        self.tmp.cleanup()

    def queue(self, task, provider=None):
        self.d.handle({"cmd": "queue-add", "item": {"task": task, "cwd": self.tmp.name, "worktree": False,
                                                    "agent_bin": "true", "provider": provider}})

    def test_budget_holds_routed_tasks_only(self):
        costs.record("anthropic", 6.0)
        self.queue("routed", provider="anthropic")
        self.queue("subscription")
        self.d.handle({"cmd": "queue-release"})
        self.assertEqual(self.started, ["subscription"])
        snap = self.d.handle({"cmd": "queue-list"})["queue"]
        self.assertIn("at the $5 daily budget", snap["blocked"]["text"])

    def test_usage_limit_holds_subscription_tasks_only(self):
        self.limit = {"agent": "claude", "name": "Claude Code", "label": "Session (5-hour)", "percent": 0.95,
                      "resetsAt": None}
        self.queue("subscription")
        self.queue("routed", provider="anthropic")
        self.d.handle({"cmd": "queue-release"})
        self.assertEqual(self.started, ["routed"])


class BalanceAndReportTest(unittest.TestCase):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"data": {"usage": 12.5, "usage_daily": 1.25, "limit": 50.0, "limit_remaining": 37.5,
                                        "is_free_tier": False}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    def test_openrouter_balance_and_report(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
                    "OMAORCHESTRA_STATE_DIR": tmp, "OMAORCHESTRA_PROVIDERS": os.path.join(tmp, "p.json"),
                    "OMAORCHESTRA_CONFIG": os.path.join(tmp, "c.toml"), "XDG_STATE_HOME": tmp}), \
                    mock.patch("omaorchestra.keys.lookup", lambda pid: "sk-or"):
                p = providers.add("openrouter", base_url=f"http://127.0.0.1:{server.server_address[1]}")
                self.assertEqual(providers.balance(p), {"usage": 12.5, "usage_daily": 1.25, "limit": 50.0,
                                                        "limit_remaining": 37.5})
                costs.record("openrouter", 0.5)
                r = spend.report(sessions=[])
                text = spend.format_report(r)
        finally:
            server.shutdown()
        self.assertIn("provider spend: $0.50 (no daily budget)", text)
        self.assertIn("openrouter: 1.25 credits used today, 12.50 in all, 37.50 of 50.00 left", text)
        self.assertIn("no usage record", text)


if __name__ == "__main__":
    unittest.main()
