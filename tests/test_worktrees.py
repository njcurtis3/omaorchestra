import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import launch, worktrees
from omaorchestra.__main__ import main


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "app"
        (self.repo / "src").mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "src" / "a.txt").write_text("one\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "first")
        self.env = mock.patch.dict(os.environ, {
            "OMAORCHESTRA_STATE_DIR": str(root / "state"), "OMAORCHESTRA_WORKTREES": str(root / "worktrees"),
            "OMAORCHESTRA_CONFIG": str(root / "none.toml"), "OMAORCHESTRA_CLAUDE": "true",
            "OMAORCHESTRA_SOCKET": str(root / "none.sock"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def commit_in(self, record, name="b.txt", text="two\n"):
        wt = Path(record["path"])
        (wt / name).write_text(text)
        git(wt, "add", name)
        git(wt, "commit", "-qm", f"add {name}")

    def test_slug(self):
        self.assertEqual(worktrees.slug("Fix the flaky LOGIN test, please!"), "fix-the-flaky-login-test-please")
        self.assertEqual(worktrees.slug("!!!"), "task")

    def test_launch_in_a_subfolder_gets_a_worktree(self):
        spawned = []
        result = launch.run("Fix the flaky test", self.repo / "src", spawn=lambda cmd, **kw: spawned.append(cmd),
                            request=lambda p: None)
        record = result["worktree"]
        self.assertIsNotNone(record)
        self.assertTrue(record["branch"].startswith("omaorchestra/fix-the-flaky-test-"))
        self.assertEqual(Path(record["workdir"]), Path(record["path"]) / "src")
        self.assertIn(f"--dir={record['workdir']}", spawned[0])
        self.assertEqual(record["base_branch"], "main")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "", "the checkout must stay clean")
        self.assertEqual(worktrees.find(result["id"][:6])["path"], record["path"])
        # --no-worktree works in the folder itself.
        plain = launch.run("x", self.repo, worktree=False, spawn=lambda cmd, **kw: None, request=lambda p: None)
        self.assertIsNone(plain["worktree"])

    def test_changes_include_commits_and_uncommitted_work(self):
        record = worktrees.create(self.repo, "task", "abcdef123")
        self.commit_in(record)
        (Path(record["path"]) / "src" / "a.txt").write_text("one\nchanged\n")
        (Path(record["path"]) / "new.txt").write_text("x\n")
        c = worktrees.changes(record)
        self.assertEqual(len(c["commits"]), 1)
        self.assertIn("+two", c["diff"])
        self.assertIn("+changed", c["diff"])
        self.assertEqual(c["untracked"], ["new.txt"])
        info = worktrees.status(record)
        self.assertTrue(info["dirty"])
        self.assertFalse(info["merged"])

    def test_merge_safety_and_success(self):
        record = worktrees.create(self.repo, "task", "abcdef123")
        with self.assertRaisesRegex(worktrees.WorktreeError, "no commits"):
            worktrees.merge(record)
        self.commit_in(record)
        (Path(record["path"]) / "b.txt").write_text("dirty\n")
        with self.assertRaisesRegex(worktrees.WorktreeError, "uncommitted changes"):
            worktrees.merge(record)
        git(record["path"], "checkout", "--", "b.txt")
        git(self.repo, "checkout", "-q", "-b", "elsewhere")
        with self.assertRaisesRegex(worktrees.WorktreeError, "not main"):
            worktrees.merge(record)
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "src" / "a.txt").write_text("local edit\n")
        with self.assertRaisesRegex(worktrees.WorktreeError, "has uncommitted changes"):
            worktrees.merge(record)
        git(self.repo, "checkout", "--", "src/a.txt")
        self.assertIn("merged", worktrees.merge(record))
        self.assertEqual((self.repo / "b.txt").read_text(), "two\n")
        self.assertTrue(worktrees.status(record)["merged"])
        self.assertIn("and its branch", worktrees.remove(record))
        self.assertEqual(worktrees.records(), [])

    def test_conflicting_merge_changes_nothing(self):
        record = worktrees.create(self.repo, "task", "abcdef123")
        self.commit_in(record, "src/a.txt", "theirs\n")
        (self.repo / "src" / "a.txt").write_text("ours\n")
        git(self.repo, "commit", "-qam", "ours")
        before = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(worktrees.WorktreeError, "conflicts"):
            worktrees.merge(record)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_remove_refuses_to_lose_work_unless_forced(self):
        record = worktrees.create(self.repo, "task", "abcdef123")
        self.commit_in(record)
        with self.assertRaisesRegex(worktrees.WorktreeError, "not merged"):
            worktrees.remove(record)
        self.assertTrue(Path(record["path"]).is_dir())
        self.assertIn("removed", worktrees.remove(record, force=True))
        self.assertFalse(Path(record["path"]).exists())
        self.assertNotIn(record["branch"], git(self.repo, "branch"))

    def test_not_a_repository(self):
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.create(self.tmp.name, "task", "abcdef123")

    def test_cli(self):
        result = launch.run("Add b", self.repo, spawn=lambda cmd, **kw: None, request=lambda p: None)
        self.commit_in(result["worktree"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(main(["worktree", "list"]), 0)
            self.assertEqual(main(["worktree", "diff", result["id"][:8]]), 0)
            self.assertEqual(main(["worktree", "remove", result["id"][:8]]), 1)  # unmerged
            self.assertEqual(main(["worktree", "merge", result["id"][:8]]), 0)
            self.assertEqual(main(["worktree", "remove", result["id"][:8]]), 0)
            self.assertEqual(main(["worktree", "list"]), 0)
        text = out.getvalue()
        for expected in ("omaorchestra/add-b-", "1 commit(s)", "+two", "not merged", "merged omaorchestra/add-b-",
                         "removed the worktree and its branch", "no worktrees"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
