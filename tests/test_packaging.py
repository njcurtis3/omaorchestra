import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra import __version__


class VersionTest(unittest.TestCase):
    """A release bumps every copy of the version together."""

    def test_pkgbuild(self):
        text = (ROOT / "packaging" / "PKGBUILD").read_text()
        self.assertEqual(re.search(r"^pkgver=(.+)$", text, re.M).group(1), __version__)

    def test_srcinfo(self):
        text = (ROOT / "packaging" / ".SRCINFO").read_text()
        self.assertEqual(re.search(r"^\tpkgver = (.+)$", text, re.M).group(1), __version__)
        self.assertIn(f"#tag=v{__version__}", text)

    def test_plugin_manifest(self):
        manifest = json.loads((ROOT / "plugin" / "omaorchestra.sessions" / "manifest.json").read_text())
        self.assertEqual(manifest["version"], __version__)


class LayoutTest(unittest.TestCase):
    def test_packaged_unit_matches_the_generated_one(self):
        from omaorchestra import service
        packaged = (ROOT / "packaging" / "omaorchestrad.service").read_text()
        self.assertEqual(packaged, service.render_unit("/usr/bin/omaorchestra", marker=False))


if __name__ == "__main__":
    unittest.main()
