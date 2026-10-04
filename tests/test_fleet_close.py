import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, fleet, fleet_close, fleet_graph, fleet_reply, top, worktrees
from omaorchestra.registry import Registry


def block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def plan_reply(n, shape):
    slices = [{"id": f"s{i}", "intent": f"Part {i}", "files": [f"part{i}.py"], "done_when": "true -> passes",
               "risk": "low", "risk_why": "small"} for i in range(1, n + 1)]
    return example("architect", shape=shape, slices=slices, edges=[])


PASS = example("reviewer", verdict="PASS", findings=[])


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


def commit(cwd, name):
    (Path(cwd) / name).write_text(f"{name}\n")
    git(cwd, "add", name)
    git(cwd, "commit", "-qm", name)


class RunHarness(unittest.TestCase):
    """A daemon driving a run in a real repository; each node's session is
    finished by hand, doing in git what its role would."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        commit(self.repo, "a.py")
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": str(self.root / "state"),
                                                "OMAORCHESTRA_CONFIG": str(self.root / "c.toml"),
                                                "OMAORCHESTRA_WORKTREES": str(self.root / "wt"),
                                                "OMAORCHESTRA_CLAUDE": "true",
                                                # merges make commits, and CI's git has no identity
                                                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        self.env.start()
        self.d = daemon.Daemon(Registry(self.root / "state" / "sessions.json"), is_alive=lambda p, s: True)
        self.d.spawn = lambda cmd, **kw: None
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None
        self.d.settings["tasks"]["max_parallel"] = 5

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def state(self):
        return fleet.load(self.run)

    def finish(self, nid, reply, work=None):
        """`work(cwd)` runs in the node's folder first (its commits)."""
        session_id = self.state()["nodes"][nid]["session"]
        session = self.d.registry.sessions[session_id]
        if work:
            work(session["cwd"])
        transcript = self.root / f"{session_id}.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": block(reply)}]}}) + "\n")
        for status in ("working", "idle"):
            self.d.handle({"cmd": "update", "session_id": session_id, "agent": "claude", "status": status,
                           "cwd": session["cwd"], "transcript_path": str(transcript)})

    def start(self, fleet_name, n, shape):
        response = self.d.handle({"cmd": "fleet-start", "goal": "Make it better", "folder": str(self.repo),
                                  "fleet": fleet_name})
        self.run = response["run"]["id"]
        self.finish("scout", example("scout"))
        self.finish("architect", plan_reply(n, shape))
        self.assertTrue(self.d.handle({"cmd": "fleet-approve", "run": self.run})["ok"])

    def build(self, sid):
        self.finish(f"builder.{sid}", example("builder"), lambda cwd: commit(cwd, f"part{sid[1:]}.py"))
        self.finish(f"reviewer.{sid}", PASS)

    def close(self, check=False):
        return self.d.handle({"cmd": "fleet-close", "run": self.run, "check": check})


class SingleLoopCloseTest(RunHarness):
    def test_closes_when_git_agrees(self):
        self.start("single-loop", 2, "single-loop")
        response = self.close(check=True)
        self.assertTrue(response["ok"])
        self.assertIn([False, "the run is still running, not finished"], [list(c) for c in response["checks"]])
        self.assertIn("still running", self.close()["error"])
        self.build("s1")
        self.build("s2")
        self.assertEqual(self.state()["status"], "done")
        branch = self.state()["branch"]
        response = self.close()
        self.assertTrue(response["ok"], response)
        self.assertTrue(all(ok for ok, _ in response["checks"]))
        self.assertIn(f"s1 built, reviewed PASS, and its commits are on {branch}", [w for _, w in response["checks"]])
        self.assertIn(f"{branch} has 2 commits to merge", [w for _, w in response["checks"]])
        self.assertIn(f"merge it with `omaorchestra worktree merge {branch}`", response["notes"][-1])
        self.assertTrue(self.state()["closed"]["head"])
        self.assertIn("already closed", self.close()["error"])
        # The run's worktree stays, and merges like any task's.
        record = worktrees.find(branch)
        self.assertEqual(record["base_branch"], "main")
        self.assertIn("merged", worktrees.merge(record))
        self.assertTrue((self.repo / "part2.py").exists())

    def test_a_split_slice_is_checked_through_its_smaller_slices(self):
        (self.root / "fleets.toml").write_text('[fleets.deep]\nshape = "single-loop"\nmax_depth = 2\n')
        response = self.d.handle({"cmd": "fleet-start", "goal": "Make it better", "folder": str(self.repo),
                                  "fleet": "deep"})
        self.run = response["run"]["id"]
        self.finish("scout", example("scout"))
        plan = plan_reply(1, "single-loop")
        plan["slices"][0]["files"] = ["pkg/"]
        self.finish("architect", plan)
        self.d.handle({"cmd": "fleet-approve", "run": self.run})
        pieces = [{"id": c, "intent": c, "files": [f"pkg/{c}.py"], "done_when": "true -> passes", "risk": "low",
                   "risk_why": "small"} for c in ("a", "b")]
        self.finish("builder.s1", {"status": "split", "changed": [],
                                   "split": {"rationale": "two parts", "slices": pieces, "edges": []}})

        def piece(name):
            def work(cwd):
                Path(cwd, "pkg").mkdir(exist_ok=True)
                commit(cwd, f"pkg/{name}.py")
            return work
        for c in ("a", "b"):
            self.finish(f"builder.s1-{c}", example("builder"), piece(c))
            self.finish(f"reviewer.s1-{c}", PASS)
        self.finish("reviewer.s1", PASS)
        self.assertEqual(self.state()["status"], "done")
        response = self.close()
        self.assertTrue(response["ok"], response)
        said = [w for _, w in response["checks"]]
        self.assertIn("s1 reviewed PASS as a whole, split into s1-a, s1-b", said)
        self.assertIn(f"s1-b built, reviewed PASS, and its commits are on {self.state()['branch']}", said)

    def test_uncommitted_work_and_no_commits_block_it(self):
        self.start("single-loop", 1, "single-loop")
        self.finish("builder.s1", example("builder"), lambda cwd: (Path(cwd) / "part1.py").write_text("x\n"))
        self.finish("reviewer.s1", PASS)
        failed = [w for ok, w in self.close(check=True)["checks"] if not ok]
        self.assertIn("s1's builder made no commits", failed)
        self.assertIn("the run's worktree has uncommitted changes", failed)
        self.assertIn(f"{self.state()['branch']} has no commits", failed)
        self.assertFalse(self.close()["ok"])

    def test_caches_from_running_the_tests_neither_hold_nor_block_it(self):
        # In a repository that does not ignore them (found in a live run).
        self.start("single-loop", 1, "single-loop")

        def work(cwd):
            commit(cwd, "part1.py")
            for name in ("__pycache__/part1.cpython-314.pyc", "tests/__pycache__/test_part1.cpython-314.pyc",
                         ".pytest_cache/v/cache/nodeids"):
                path = Path(cwd) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
        self.finish("builder.s1", example("builder"), work)
        self.assertNotEqual(self.state()["status"], "held", self.state().get("reason"))
        self.finish("reviewer.s1", PASS)
        response = self.close()
        self.assertTrue(response["ok"], response)

    def test_a_builder_that_left_its_branch_holds_the_run(self):
        self.start("single-loop", 1, "single-loop")
        self.finish("builder.s1", example("builder"), lambda cwd: git(cwd, "checkout", "-q", "-b", "elsewhere"))
        state = self.state()
        self.assertEqual(state["status"], "held")
        self.assertEqual(state["reason"], f"builder.s1 ended on elsewhere, not {state['branch']}")

    def test_closing_is_refused_from_inside_an_agent_but_checking_is_not(self):
        self.start("single-loop", 1, "single-loop")
        with mock.patch.object(self.d, "from_agent", lambda pid: "claude"):
            self.assertIn("inside an agent", self.d.handle_fleet("fleet-close", {"run": self.run}, peer=1)["error"])
            self.assertTrue(self.d.handle_fleet("fleet-close", {"run": self.run, "check": True}, peer=1)["ok"])


class DiamondCloseTest(RunHarness):
    def integrate(self, merged=("s1", "s2", "s3")):
        def merge(cwd):
            for sid in merged:
                git(cwd, "merge", "--no-ff", "-q", "-m", f"merge {sid}", self.state()["slice_worktrees"][sid]["branch"])
        self.finish("integrator", example("integrator", merged=list(("s1", "s2", "s3"))), merge)

    def test_closing_removes_the_slices_worktrees(self):
        self.start("diamond", 3, "diamond")
        for sid in ("s1", "s2", "s3"):
            self.build(sid)
        self.d.handle({"cmd": "fleet-approve", "run": self.run, "gate": "merge"})
        self.integrate()
        self.assertEqual(self.state()["status"], "done")
        slice_paths = [r["path"] for r in self.state()["slice_worktrees"].values()]
        response = self.close()
        self.assertTrue(response["ok"], response)
        self.assertIn("the integrator merged every slice", [w for _, w in response["checks"]])
        self.assertTrue(all(not Path(p).exists() for p in slice_paths))
        self.assertTrue(Path(self.state()["worktree"]["path"]).exists())
        self.assertTrue(all(n.startswith("s") for n in response["notes"][:3]))

    def test_a_slice_the_integrator_did_not_merge(self):
        self.start("diamond", 3, "diamond")
        for sid in ("s1", "s2", "s3"):
            self.build(sid)
        self.d.handle({"cmd": "fleet-approve", "run": self.run, "gate": "merge"})
        # It says it merged all three; git says s3 is missing.
        self.integrate(merged=("s1", "s2"))
        self.assertEqual(self.state()["status"], "done")
        failed = [w for ok, w in self.close(check=True)["checks"] if not ok]
        self.assertEqual(len(failed), 1)
        self.assertRegex(failed[0], r"^s3's commits \(\w+\) are not on omaorchestra/fleet-")

    def test_the_integrator_must_merge_every_slice_and_stay_on_the_branch(self):
        self.start("diamond", 3, "diamond")
        for sid in ("s1", "s2", "s3"):
            self.build(sid)
        self.d.handle({"cmd": "fleet-approve", "run": self.run, "gate": "merge"})
        self.finish("integrator", example("integrator", merged=["s1", "s2"]))
        self.assertEqual(self.state()["reason"], "integrator: it did not merge s3")


class TopCloseTest(unittest.TestCase):
    def test_close_from_top(self):
        run = {"id": "r1", "goal": "Make it better", "status": "done", "gate": None, "folder": "/w/app",
               "branch": "omaorchestra/fleet-r1", "nodes": {}, "updated": 1, "shape": "single-loop"}
        requests = []

        def request(payload, timeout=1.0):
            requests.append(payload)
            return {"ok": True, "run": {**run, "closed": {"at": 2}}}
        t = top.Top(request=request, stop=lambda s: None, wait=lambda s: True, now=lambda: 10, agents=["claude"],
                    background=lambda work: work())
        t.apply({"ok": True, "sessions": [], "queue": {"tasks": []}, "away": None, "approvals": [], "fleets": [run]})
        t.switch("fleets")
        self.assertIn(" c Close… ", t.render(40, 20).text())
        t.key("c")
        t.key("y")
        self.assertEqual(requests[-1], {"cmd": "fleet-close", "run": "r1"})
        self.assertIn("closed", t.render(40, 20).text())
        self.assertNotIn(" c Close… ", t.render(40, 20).text())


if __name__ == "__main__":
    unittest.main()
