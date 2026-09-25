import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
