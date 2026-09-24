import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra import setup

BINDINGS = """-- Application bindings
o.bind("SUPER + RETURN", "Terminal", "uwsm-app -- xdg-terminal-exec")
"""
HYPRLAND = """require("default.hypr.omarchy")
require("hypr.bindings")
"""
MENU = """{
  // Extend the Quickshell Omarchy menu with JSONC.
  "personal": {"icon":"","label":"Personal"},
  "personal.notes": {"icon":"","label":"Notes","action":"notes"}
}
"""


def strip_jsonc(raw):
    """Omarchy's MenuModel.stripJsonc."""
    raw = re.sub(r"^\s*//[^\n]*(\n|$)", "", raw, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", raw)


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.config = self.home / ".config"
        (self.config / "hypr").mkdir(parents=True)
        (self.config / "hypr" / "bindings.lua").write_text(BINDINGS)
        (self.config / "hypr" / "hyprland.lua").write_text(HYPRLAND)
        (self.config / "omarchy" / "extensions").mkdir(parents=True)
        (self.config / "omarchy" / "extensions" / "omarchy-menu.jsonc").write_text(MENU)
        (self.home / ".claude").mkdir()
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        for tool in ("omarchy-bar", "omarchy-plugin"):
            (bin_dir / tool).write_text("#!/bin/sh\n")
            (bin_dir / tool).chmod(0o755)
        self.env = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"), "PATH": f"{bin_dir}:/usr/bin:/bin",
            "OMAORCHESTRA_CONFIG": str(self.home / "none.toml"), "HYPRLAND_INSTANCE_SIGNATURE": "",
        })
        self.env.start()
        self.binary = str(ROOT / "bin" / "omaorchestra")
        self.run_ = Runner()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def setup(self, **kwargs):
        return setup.setup(self.binary, skip=("service",), run=self.run_, **kwargs)

    def teardown(self, **kwargs):
        return setup.teardown(skip=("service",), run=self.run_, **kwargs)

    def test_round_trip_leaves_user_files_as_they_were(self):
        self.setup()
        bindings = (self.config / "hypr" / "bindings.lua").read_text()
        self.assertIn('o.bind("SUPER + ALT + A"', bindings)
        self.assertTrue(bindings.startswith(BINDINGS))
        self.assertIn('o.window("^omaorchestra$"', (self.config / "hypr" / "hyprland.lua").read_text())
        self.assertTrue((self.config / "omarchy" / "plugins" / "omaorchestra.sessions" / "BarWidget.qml").exists())
        self.assertIn(["omarchy-bar", "put", "omaorchestra.sessions"], self.run_.calls)
        self.assertIn("hooks", json.loads((self.home / ".claude" / "settings.json").read_text()))

        self.teardown()
        self.assertEqual((self.config / "hypr" / "bindings.lua").read_text(), BINDINGS)
        self.assertEqual((self.config / "hypr" / "hyprland.lua").read_text(), HYPRLAND)
        self.assertEqual((self.config / "omarchy" / "extensions" / "omarchy-menu.jsonc").read_text(), MENU)
        self.assertFalse((self.config / "omarchy" / "plugins" / "omaorchestra.sessions").exists())
        self.assertIn(["omarchy-plugin", "disable", "omaorchestra.sessions"], self.run_.calls)
        self.assertNotIn("omaorchestra", (self.home / ".claude" / "settings.json").read_text())

    def test_menu_stays_valid_for_omarchy(self):
        self.setup(only=("menu",))
        menu = json.loads(strip_jsonc((self.config / "omarchy" / "extensions" / "omarchy-menu.jsonc").read_text()))
        self.assertEqual(menu["omaorchestra.app"]["action"], "omaorchestra app")
        self.assertIn("personal.notes", menu)

    def test_menu_is_created_when_missing(self):
        (self.config / "omarchy" / "extensions" / "omarchy-menu.jsonc").unlink()
        self.setup(only=("menu",))
        menu = json.loads(strip_jsonc((self.config / "omarchy" / "extensions" / "omarchy-menu.jsonc").read_text()))
        self.assertIn("omaorchestra.focus", menu)

    def test_second_run_changes_nothing(self):
        self.setup()
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and "backup" not in p.name}
        lines = self.setup()
        after = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file() and "backup" not in p.name}
        self.assertEqual(before, after)
        self.assertFalse([line for line in lines if "updated" in line or "copied" in line], lines)

    def test_hand_made_entries_are_left_alone(self):
        own = BINDINGS + 'o.bind("SUPER + ALT + A", "Agents", "omaorchestra focus")\n'
        (self.config / "hypr" / "bindings.lua").write_text(own)
        lines = self.setup(only=("bindings",))
        self.assertEqual((self.config / "hypr" / "bindings.lua").read_text(), own)
        self.assertTrue(any("left alone" in line for line in lines))
        self.teardown(only=("bindings",))
        self.assertEqual((self.config / "hypr" / "bindings.lua").read_text(), own)

    def test_dry_run_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        lines = self.setup(dry_run=True)
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob("*") if p.is_file()})
        self.assertTrue(any(line.startswith("bindings: would add") for line in lines))
        self.assertEqual(self.run_.calls, [])

    def test_missing_hyprland_config_is_skipped(self):
        (self.config / "hypr" / "bindings.lua").unlink()
        self.assertIn("skipped", self.setup(only=("bindings",))[0])

    def test_agents_that_are_not_installed_get_no_hooks(self):
        lines = setup.setup_hooks(self.binary, dry_run=False, agents=["claude", "codex"])
        self.assertFalse((self.home / ".codex").exists())
        self.assertTrue(any("skipped Codex" in line for line in lines))

    def test_unknown_step(self):
        with self.assertRaises(setup.SetupError):
            setup.selected(only=("bogus",))

    def test_launcher_for_a_checkout(self):
        lines = self.setup(only=("launcher",))
        link = self.home / ".local" / "bin" / "omaorchestra"
        self.assertEqual(os.readlink(link), self.binary)
        self.assertTrue((self.home / ".local" / "share" / "applications" / "omaorchestra.desktop").exists())
        self.assertTrue(lines)
        self.teardown(only=("launcher",))
        self.assertFalse(link.exists() or link.is_symlink())


if __name__ == "__main__":
    unittest.main()
