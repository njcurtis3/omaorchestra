import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def load_script(name):
    """A script from scripts/ (no .py) as a module."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(ROOT / "scripts" / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class CommandsPageTest(unittest.TestCase):
    def test_docs_commands_md_is_current(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "commands"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or "run scripts/commands")

    def test_every_command_has_help(self):
        text = (ROOT / "docs" / "commands.md").read_text()
        self.assertNotIn("|  |", text, "an argument has no help text")
        for command in ("away", "remote test", "queue add", "mcp profile set", "setup"):
            self.assertIn(f"`omaorchestra {command}`", text)


if __name__ == "__main__":
    unittest.main()


class FleetsPageTest(unittest.TestCase):
    def test_every_reply_field_is_in_the_reference(self):
        # docs/fleets.md lists what each role hands back; keep it in step with the contracts.
        from omaorchestra import fleet_reply
        text = (ROOT / "docs" / "fleets.md").read_text()

        def fields(spec):
            if isinstance(spec, fleet_reply.Fields):
                for name, inner in {**spec.required, **spec.optional}.items():
                    yield name
                    yield from fields(inner)
            elif isinstance(spec, fleet_reply.Many):
                yield from fields(spec.item)

        for role, contract in fleet_reply.CONTRACTS.items():
            for name in fields(contract):
                self.assertIn(f"`{name}`", text, f"{role}'s `{name}` is not in docs/fleets.md")

    def test_every_fleet_key_is_in_the_reference(self):
        from omaorchestra import fleet_graph
        text = (ROOT / "docs" / "fleets.md").read_text()
        for key in fleet_graph.KEYS:
            self.assertIn(f"`{key}", text, f"fleets.toml's `{key}` is not in docs/fleets.md")


class SectionsLineTest(unittest.TestCase):
    def test_every_doc_lists_its_sections(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "doc-sections"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or "run scripts/doc-sections")

    def test_anchors_match_github(self):
        ds = load_script("doc-sections")
        self.assertEqual(ds.slug("In a terminal (and on your phone)"), "in-a-terminal-and-on-your-phone")
        self.assertEqual(ds.slug("`omaorchestra fleet run`"), "omaorchestra-fleet-run")
        self.assertEqual(ds.slug("A session never appears"), "a-session-never-appears")
        text = "# T\n\n## Setup\n\n```\n## not a heading\n```\n\n### Setup\n\n## Use it\n"
        self.assertEqual([h[2] for h in ds.headings(text)], ["t", "setup", "setup-1", "use-it"])
        once = ds.apply(text)
        self.assertIn("**Sections:** [Setup](#setup) · [Use it](#use-it)", once)
        self.assertEqual(ds.apply(once), once)  # rerunning changes nothing
