import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


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
