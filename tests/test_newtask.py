import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import launch, recent
from omaorchestra.app import present


class RecentTest(unittest.TestCase):
    def test_most_recent_first_unique_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": tmp}):
            self.assertEqual(recent.load(), [])
            for f in ["/a", "/b", "/a"] + [f"/x{i}" for i in range(12)]:
                recent.add(f)
            folders = recent.load()
            self.assertEqual(folders[0], "/x11")
            self.assertEqual(len(folders), recent.LIMIT)
            self.assertEqual(len(set(folders)), len(folders))
            (Path(tmp) / "recent-folders.json").write_text("{not json")
            self.assertEqual(recent.load(), [])

    def test_launch_remembers_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"OMAORCHESTRA_STATE_DIR": tmp, "OMAORCHESTRA_CLAUDE": "true",
                             "OMAORCHESTRA_CONFIG": os.path.join(tmp, "none.toml")}):
            launch.run("x", tmp, spawn=lambda cmd, **kw: None, request=lambda p: None)
            self.assertEqual(recent.load(), [str(Path(tmp).resolve())])

    def test_recent_folders_merge(self):
        exists = lambda f: f != "/gone"  # noqa: E731
        folders = present.recent_folders(["/b", "/gone", "/a"], [{"cwd": "/a"}, {"cwd": "/c"}, {}], exists=exists)
        self.assertEqual(folders, ["/b", "/a", "/c"])


if __name__ == "__main__":
    unittest.main()
