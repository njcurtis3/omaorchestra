import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import config, launch, modeldefaults
from omaorchestra.__main__ import main
from omaorchestra.app import present


class DefaultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "proj" / "sub").mkdir(parents=True)
        self.proj = root / "proj"
        self.cfg = root / "config.toml"
        self.env = mock.patch.dict(os.environ, {
            "OMAORCHESTRA_STATE_DIR": str(root / "state"), "OMAORCHESTRA_CONFIG": str(self.cfg),
            "OMAORCHESTRA_CLAUDE": "true", "OMAORCHESTRA_SOCKET": str(root / "none.sock")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_folder_then_global_then_none(self):
        self.assertEqual(modeldefaults.for_folder(self.proj), (None, None))
        config.save({"tasks": {"default_model": "sonnet"}})
        self.assertEqual(modeldefaults.for_folder(self.proj / "sub"), ("sonnet", "global"))
        modeldefaults.set_for(self.proj, "opus")
        self.assertEqual(modeldefaults.for_folder(self.proj / "sub"), ("opus", "folder"), "inherited from a parent")
        modeldefaults.set_for(self.proj, "")
        self.assertEqual(modeldefaults.for_folder(self.proj), ("sonnet", "global"))

    def test_launch_uses_the_default_unless_given_one(self):
        modeldefaults.set_for(self.proj, "haiku")
        spawned = []
        launch.run("x", self.proj, worktree=False, spawn=lambda cmd, **kw: spawned.append(cmd), request=lambda p: None)
        launch.run("x", self.proj, model="opus", worktree=False, spawn=lambda cmd, **kw: spawned.append(cmd),
                   request=lambda p: None)
        models = [cmd[cmd.index("--model") + 1] for cmd in spawned]
        self.assertEqual(models, ["haiku", "opus"])

    def test_config_rejects_a_bad_model_name(self):
        with self.assertRaises(config.ConfigError):
            config.save({"tasks": {"default_model": "two words"}})

    def test_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["models", "default", "opus", "--for", str(self.proj)])
            main(["models", "default", "--for", str(self.proj / "sub")])
            main(["models", "default", "sonnet"])
            main(["models", "default", "--clear", "--for", str(self.proj)])
            main(["models", "default", "--for", str(self.proj)])
        lines = out.getvalue().splitlines()
        self.assertIn("opus  (this folder's default)", lines)
        self.assertEqual(lines[-1], "sonnet  (the global default)")


class ChoicesTest(unittest.TestCase):
    def test_default_aliases_then_claude_ids(self):
        catalog_models = [{"id": "opus", "name": "Opus (latest)", "provider": "claude-code"},
                          {"id": "claude-opus-5", "name": "Claude Opus 5", "provider": "anthropic"},
                          {"id": "claude-brand-new", "name": "New", "provider": "anthropic"},
                          {"id": "gpt-5", "name": "gpt-5", "provider": "openai"}]
        choices = present.model_choices(catalog_models, default="haiku")
        values = [c["value"] for c in choices]
        self.assertEqual(choices[0], {"value": "", "label": "Default (haiku)"})
        self.assertEqual(values[1], "opus")
        self.assertIn("claude-brand-new", values, "catalog Claude models are offered")
        self.assertIn("claude-sonnet-5", values, "known Claude models are offered without a provider")
        self.assertNotIn("gpt-5", values, "Claude Code cannot use other providers' models directly")


if __name__ == "__main__":
    unittest.main()
