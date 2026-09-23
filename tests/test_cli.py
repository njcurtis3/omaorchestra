import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __version__


class VersionTest(unittest.TestCase):
    def test_version_is_set(self):
        self.assertTrue(__version__)


if __name__ == "__main__":
    unittest.main()
