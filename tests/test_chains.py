import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __main__ as cli, chain, daemon, history, recipes, taskqueue, worktrees
from omaorchestra.registry import Registry


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


class ChainDaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "work"
        self.folder.mkdir()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml"),
                                                "OMAORCHESTRA_WORKTREES": os.path.join(self.tmp.name, "wt")})
        self.env.start()
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "state" / "sessions.json"), is_alive=lambda p, s: False)
        self.spawned = []
        self.d.spawn = lambda cmd, **kw: self.spawned.append(cmd)
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None
        self.d.settings["tasks"]["max_parallel"] = 1

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def add(self, task, **extra):
        item = {"task": task, "cwd": str(self.folder), "worktree": False, "agent_bin": "true", "path": "/usr/bin:/bin",
                **extra}
        response = self.d.handle({"cmd": "queue-add", "item": item})
        self.assertTrue(response["ok"], response)
        return response["item"]

    def started(self):
        """The session id of the task started last."""
        return [sid for sid, s in self.d.registry.sessions.items() if s.get("launching")][-1]

    def status(self, sid, status, **extra):
        self.d.handle({"cmd": "update", "session_id": sid, "agent": "claude", "status": status, **extra})

    def task(self, item_id):
        return next(t for t in self.d.queue.tasks if t["id"] == item_id)

    def test_released_when_the_step_before_goes_idle_after_working(self):
        first = self.add("Write the parser")
        second = self.add("Write its tests", after=first["id"][:6], same_worktree=True)
        self.assertEqual(self.task(second["id"])["state"], "waiting")
        self.assertEqual((second["chain"], second["step"]), (first["id"], 2))
        sid = self.started()
        self.assertEqual(self.task(second["id"])["parent_session"], sid)
        self.assertEqual(self.d.registry.sessions[sid]["chain"], first["id"])
        self.status(sid, "needs-input", message="Allow Bash?")  # waiting for you: the chain waits too
        self.assertEqual(self.task(second["id"])["state"], "waiting")
        self.status(sid, "working")
        self.status(sid, "idle")  # done
        # It started at once (a slot is free: the first step is idle).
        self.assertFalse(any(t["id"] == second["id"] for t in self.d.queue.tasks))
        child = self.d.registry.sessions[self.started()]
        self.assertTrue(child["task"].startswith("Write its tests\n\nThis task follows on from an earlier step"))
        self.assertIn("Write the parser", child["task"])
        self.assertEqual((child["step"], child["cwd"]), (2, str(self.folder)))

    def test_held_when_the_step_before_crashes(self):
        first = self.add("Build it")
        second = self.add("Review it", after=first["id"])
        sid = self.started()
        self.d.registry.sessions[sid].update(pid=1, pid_start=1)
        self.status(sid, "working")
        self.d.prune()  # its process is gone while working
        held = self.task(second["id"])
        self.assertEqual((held["state"], held["error"]), ("held", "the step before it crashed"))
        self.assertEqual(self.spawned.__len__(), 1, "a held step never starts")
        self.d.handle({"cmd": "queue-resume", "id": second["id"]})
        self.assertFalse(any(t["id"] == second["id"] for t in self.d.queue.tasks), "resumed by hand, it runs")

    def test_cancelling_a_step_holds_the_rest(self):
        self.d.handle({"cmd": "queue-hold"})
        first = self.add("One")
        second = self.add("Two", after=first["id"])
        self.d.handle({"cmd": "queue-cancel", "id": first["id"]})
        self.assertEqual(self.task(second["id"])["error"], "the task before it was cancelled")
        with self.assertRaises(taskqueue.QueueError):
            self.d.queue.set_state(self.add("Three", after=second["id"])["id"], "paused")

    def test_the_step_limit(self):
        self.d.settings["tasks"]["max_chain_steps"] = 2
        self.d.handle({"cmd": "queue-hold"})
        first = self.add("One")
        second = self.add("Two", after=first["id"])
        response = self.d.handle({"cmd": "queue-add", "item": {"task": "Three", "cwd": str(self.folder),
                                                                "after": second["id"]}})
        self.assertFalse(response["ok"])
        self.assertIn("the limit is 2", response["error"])

    def test_after_a_running_session_and_after_one_already_done(self):
        self.status("live-1", "working", cwd=str(self.folder))
        waiting = self.add("Then this", after="live-1")
        self.assertEqual(self.task(waiting["id"])["parent_session"], "live-1")
        self.status("live-1", "idle")
        self.assertFalse(any(t["id"] == waiting["id"] for t in self.d.queue.tasks))
        self.status("done-1", "idle", cwd=str(self.folder))
        self.d.handle({"cmd": "queue-hold"})
        released = self.add("And this", after="done-1")
        self.assertEqual(self.task(released["id"])["state"], "pending", "what it follows had already finished")

    def test_a_review_step_gets_the_diff_and_its_verdict_is_saved(self):
        git(self.folder, "init", "-q", "-b", "main")
        (self.folder / "a.py").write_text("x = 1\n")
        git(self.folder, "add", ".")
        git(self.folder, "commit", "-q", "-m", "start")
        first = self.add("Make x two")
        review = self.add("Review it", after=first["id"], review=True)
        sid = self.started()
        self.assertTrue(self.d.registry.sessions[sid].get("git_start"))
        (self.folder / "a.py").write_text("x = 2\n")
        self.status(sid, "working")
        self.status(sid, "idle")
        reviewer_id = self.started()
        reviewer = self.d.registry.sessions[reviewer_id]
        self.assertIn("-x = 1", reviewer["task"])
        self.assertIn("+x = 2", reviewer["task"])
        self.assertIn("Verdict: ready", reviewer["task"])
        self.assertTrue(reviewer["review"])
        transcript = Path(self.tmp.name) / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "One problem: x should be a constant.\n\nVerdict: needs work"}]}}) + "\n")
        self.status(reviewer_id, "working", transcript_path=str(transcript))
        self.status(reviewer_id, "idle")
        saved = Path(self.tmp.name) / "state" / "reviews" / f"{reviewer_id}.md"
        self.assertTrue(saved.exists())
        self.assertIn("# Review: needs work", saved.read_text())

    def test_nothing_to_review_holds_the_review(self):
        first = self.add("Not a repository")
        review = self.add("Review it", after=first["id"], review=True)
        sid = self.started()
        self.status(sid, "idle")
        self.assertEqual(self.task(review["id"])["state"], "held")
        self.assertIn("not run in a git repository", self.task(review["id"])["error"])


class VerdictTest(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(chain.verdict("Looks good.\nVerdict: ready"), "ready")
        self.assertEqual(chain.verdict("Problems.\n**Verdict: needs work**"), "needs work")
        self.assertEqual(chain.verdict("`Verdict: Needs Work.`"), "needs work")
        self.assertEqual(chain.verdict("Verdict: approve"), "ready")
        self.assertEqual(chain.verdict("I have thoughts."), "unclear")
        self.assertEqual(chain.verdict("Verdict: maybe"), "unclear")

    def test_review_prompt_asks_for_no_tools(self):
        text = chain.review_task("Review the login fix", "diff --git a b\n+x", truncated=True)
        self.assertIn("cut short", text)
        self.assertIn("do not run any commands", text)
        self.assertTrue(text.startswith("Review the login fix"))


class WorktreeReviewTest(unittest.TestCase):
    def test_review_is_written_beside_the_worktree_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": tmp}):
            wt = Path(tmp) / "wt" / "site-fix"
            wt.mkdir(parents=True)
            worktrees._save([{"session_id": "s1", "path": str(wt), "branch": "omaorchestra/fix", "base": "abc",
                              "repo": tmp}])
            transcript = Path(tmp) / "t.jsonl"
            transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "All fine.\nVerdict: ready"}]}}) + "\n")
            review = chain.record_review({"id": "r1", "agent": "codex", "worktree": str(wt),
                                          "transcript_path": str(transcript)}, now=1_790_000_000)
            self.assertEqual(review["verdict"], "ready")
            self.assertEqual(review["file"], str(wt) + ".review.md")
            self.assertFalse((wt / "review.md").exists(), "never inside the worktree")
            self.assertEqual(worktrees.find("s1")["review"]["verdict"], "ready")


class RecipeTest(unittest.TestCase):
    def test_builtins_and_items(self):
        steps = recipes.items("build-then-review", "Fix the login", "/w", base={"worktree": True, "model": "opus"})
        self.assertEqual(len(steps), 2)
        self.assertTrue(steps[0]["task"].startswith("Fix the login"))
        self.assertTrue(steps[0]["worktree"])
        self.assertTrue(steps[1]["review"] and steps[1]["same_worktree"])
        self.assertNotIn("worktree", steps[1])
        plan = recipes.items("plan-then-build", "Add dark mode", "/w")
        self.assertEqual(plan[0]["permission_mode"], "acceptEdits")
        self.assertIn("PLAN.md", plan[1]["task"])

    def test_your_own_and_mistakes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recipes.toml"
            path.write_text('[recipes.fix-and-test]\ndescription = "d"\n'
                            '[[recipes.fix-and-test.steps]]\nprompt = "{task}"\n'
                            '[[recipes.fix-and-test.steps]]\nprompt = "Test {task}"\nagent = "codex"\n')
            loaded = recipes.load(path)
            self.assertIn("fix-and-test", loaded)
            self.assertIn("plan-then-build", loaded)
            self.assertEqual(recipes.items("fix-and-test", "x", "/w", target=path)[1]["agent"], "codex")
            for bad in ('[recipes.a]\n[[recipes.a.steps]]\nprompt = "no placeholder"\n',
                        '[recipes.a]\n[[recipes.a.steps]]\nprompt = "{task}"\nreview = true\n',
                        '[recipes.a]\n[[recipes.a.steps]]\nprompt = "{task}"\nagent = "gpt"\n',
                        '[recipes.a]\n[[recipes.a.steps]]\nprompt = "{task}"\ncolour = "red"\n',
                        'not toml ['):
                path.write_text(bad)
                with self.assertRaises(recipes.RecipeError):
                    recipes.load(path)
        with self.assertRaises(recipes.RecipeError):
            recipes.get("nope")


class CliTest(unittest.TestCase):
    def run_cli(self, *argv, answer=None):
        sent = []

        def request(payload, timeout=30):
            sent.append(payload)
            return answer(payload) if answer else {"ok": True}
        out = io.StringIO()
        with mock.patch.object(cli.client, "request", request), redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue(), sent

    def test_queue_add_after(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = {"held": False, "busy": 0, "limit": 2, "tasks": []}
            _, _, sent = self.run_cli("queue", "add", "Then test it", "--in", folder, "--after", "abc123",
                                      "--same-worktree",
                                      answer=lambda p: {"ok": True, "item": {"id": "def456"}, "queue": queue})
        item = sent[0]["item"]
        self.assertEqual((item["after"], item["same_worktree"], item["brief"]), ("abc123", True, True))

    def test_recipe_run_queues_a_chain(self):
        counter = iter(range(1, 10))
        queue = {"held": False, "busy": 0, "limit": 2, "tasks": [
            {"id": "11111111", "task": "Fix it", "cwd": "/w", "state": "pending", "step": 1, "chain": "11111111",
             "recipe": "build-then-review"},
            {"id": "22222222", "task": "Review", "base_task": "Review the changes", "cwd": "/w", "state": "waiting",
             "after": "11111111", "step": 2, "recipe": "build-then-review"}]}
        with tempfile.TemporaryDirectory() as folder:
            code, out, sent = self.run_cli("recipe", "run", "build-then-review", "Fix it", "--in", folder,
                                           answer=lambda p: {"ok": True, "item": {"id": f"{next(counter)}" * 8},
                                                             "queue": queue})
        self.assertEqual(code, 0)
        self.assertEqual(len(sent), 2)
        self.assertNotIn("after", sent[0]["item"])
        self.assertEqual(sent[1]["item"]["after"], "1" * 8)
        self.assertTrue(sent[1]["item"]["review"])
        self.assertIn("↳ Review the changes", out)
        self.assertIn("waits for 11111111 to finish", out)
        self.assertIn("step 2 of build-then-review", out)


if __name__ == "__main__":
    unittest.main()
