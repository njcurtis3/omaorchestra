import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, launch, modeldefaults, providers, routing
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry

KEYS = {"anthropic": "sk-ant-test", "openrouter": "sk-or-test", "proxy": "sk-proxy"}


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "OMAORCHESTRA_PROVIDERS": os.path.join(self.tmp.name, "p.json"),
            "OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
            "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "none.toml"),
            "OMAORCHESTRA_SOCKET": os.path.join(self.tmp.name, "none.sock"),
            "OMAORCHESTRA_CLAUDE": "true"})
        self.env.start()
        self.keys = mock.patch("omaorchestra.keys.lookup", KEYS.get)
        self.keys.start()
        providers.add("anthropic")
        providers.add("openrouter")
        providers.add("anthropic", provider_id="proxy", base_url="https://gateway.example.com")
        providers.add("ollama")

    def tearDown(self):
        self.keys.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_environment_per_provider(self):
        self.assertEqual(routing.claude_code_env(providers.get("anthropic")), {"ANTHROPIC_API_KEY": "sk-ant-test"})
        self.assertEqual(routing.claude_code_env(providers.get("proxy")),
                         {"ANTHROPIC_API_KEY": "sk-proxy", "ANTHROPIC_BASE_URL": "https://gateway.example.com"})
        self.assertEqual(routing.claude_code_env(providers.get("openrouter"), "anthropic/claude-sonnet-5"),
                         {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": "sk-or-test",
                          "ANTHROPIC_API_KEY": ""})

    def test_refusals(self):
        cases = [(providers.get("ollama"), None, "cannot run through"),
                 (providers.get("openrouter"), "openai/gpt-5", "not a Claude model"),
                 (providers.get("anthropic"), "gpt-5", "not a Claude model")]
        for provider, model, message in cases:
            with self.assertRaisesRegex(routing.RoutingError, message):
                routing.claude_code_env(provider, model)
        with self.assertRaisesRegex(routing.RoutingError, "no API key"):
            routing.claude_code_env(providers.get("anthropic"), lookup=lambda pid: None)

    def test_launch_passes_the_environment_and_skips_folder_defaults(self):
        modeldefaults.set_for(self.tmp.name, "opus")
        spawned = []
        result = launch.run("x", self.tmp.name, worktree=False, provider="openrouter",
                            model="anthropic/claude-sonnet-5",
                            spawn=lambda cmd, **kw: spawned.append((cmd, kw["env"])), request=lambda p: None)
        cmd, env = spawned[0]
        self.assertEqual((env["ANTHROPIC_AUTH_TOKEN"], env["ANTHROPIC_API_KEY"]), ("sk-or-test", ""))
        self.assertEqual(cmd[cmd.index("--model") + 1], "anthropic/claude-sonnet-5")
        self.assertIn("PATH", env, "the rest of the environment is kept")
        spawned.clear()
        launch.run("x", self.tmp.name, worktree=False, provider="anthropic",
                   spawn=lambda cmd, **kw: spawned.append((cmd, kw["env"])), request=lambda p: None)
        self.assertNotIn("--model", spawned[0][0], "the folder's subscription default is not used when routed")
        with self.assertRaisesRegex(launch.LaunchError, "cannot run through"):
            launch.run("x", self.tmp.name, worktree=False, provider="ollama", spawn=lambda *a, **k: None,
                       request=lambda p: None)
        self.assertTrue(result["id"])

    def test_queued_tasks_store_only_the_provider_id(self):
        settings = daemon.config.defaults()
        d = daemon.Daemon(Registry(Path(self.tmp.name) / "sessions.json"), settings=settings)
        d.usage_check = lambda agent, threshold: None
        d.usage_refresh = lambda agent: None
        spawned = []
        d.spawn = lambda cmd, **kw: spawned.append(kw["env"])
        d.handle({"cmd": "queue-hold"})
        d.handle({"cmd": "queue-add", "item": {"task": "t", "cwd": self.tmp.name, "worktree": False,
                                               "agent_bin": "true", "provider": "anthropic"}})
        stored = Path(self.tmp.name, "queue.json").read_text()
        self.assertIn('"provider": "anthropic"', stored)
        self.assertNotIn("sk-ant-test", stored, "no key in the queue file")
        d.handle({"cmd": "queue-release"})
        self.assertEqual(spawned[0]["ANTHROPIC_API_KEY"], "sk-ant-test", "the key is read when the task starts")
        (session,) = d.registry.sessions.values()
        self.assertEqual(session["provider"], "anthropic")
        self.assertNotIn("sk-ant-test", Path(self.tmp.name, "sessions.json").read_text())

    def test_queue_add_refuses_a_bad_provider_at_once(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch("omaorchestra.client.request") as request:
            code = main(["queue", "add", "t", "--in", self.tmp.name, "--provider", "ollama"])
        self.assertEqual(code, 1)
        self.assertIn("cannot run through", err.getvalue())
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
