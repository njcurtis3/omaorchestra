"""Closing a fleet run: checked against git, not against what the state says.

A run may be closed once it has finished and git agrees:

  - every slice was built (done, not blocked) and its latest build was
    reviewed PASS; a slice a builder split, reviewed PASS as a whole, and
    each of its smaller slices so;
  - the commits each slice's builder ended at are on the run's branch (in a
    diamond, the integrator merged them there);
  - the run's branch has commits of its own, and its worktree and each
    slice's are clean (nothing left uncommitted);
  - a diamond's integrator merged every slice and its full suite passed;
  - every file the run's branch changed is in a slice's approved files, or
    one you accepted (the scope check, fleet_scope.py, across the branch).

Closing removes the slices' worktrees (their work is on the run's branch;
a worktree that would lose work is kept, and said so) and keeps the run's
own worktree and branch: merging that into your branch stays yours, with
`omaorchestra worktree merge`, as for any task's worktree.

Adapted from graph_agents' close-run (github.com/njcurtis3/graph_agents).
"""

import time
from pathlib import Path

from . import fleet, fleet_graph, fleet_scope, worktrees


def _git(cwd, *args):
    try:
        result = worktrees.git(cwd, *args, check=False)
    except worktrees.WorktreeError:
        return None
    return result


def _clean(path):
    """None when the worktree at `path` is clean, else why not."""
    if not Path(path).is_dir():
        return f"{path} is gone"
    result = _git(path, "status", "--porcelain", "--untracked-files=all")
    if result is None or result.returncode != 0:
        return f"git cannot read {path}"
    # New caches from running the tests (fleet_scope.is_cache) do not count.
    left = [line for line in result.stdout.splitlines()
            if line.strip() and not (line.startswith("?? ") and fleet_scope.is_cache(line[3:]))]
    return "has uncommitted changes" if left else None


def check(state):
    """[(ok, what)] for every check, in order; the run may close only when
    all are ok."""
    checks = []

    def add(ok, what):
        checks.append((bool(ok), what))

    words = {"running": "still running", "at-gate": f"waiting at the {state.get('gate')} gate", "held": "held",
             "cancelled": "cancelled"}
    add(state["status"] == "done", "the run finished" if state["status"] == "done"
        else f"the run is {words.get(state['status'], state['status'])}, not finished")
    plan = fleet.live_plan(state)
    if not plan:
        add(False, "there is no approved plan")
        return checks
    own = state.get("worktree")
    in_git = bool(state.get("repo") and own)

    def built_and_passed(s):
        """A slice built and reviewed PASS with its commits on the run's
        branch; a split one, reviewed PASS as a whole, and each smaller slice so."""
        sid = s["id"]
        built = fleet.latest(state, "builder", sid)
        split = fleet.split_plan(built) if built else None
        if not built or built["status"] != "done" or (built["result"]["status"] != "done" and not split):
            add(False, f"{sid} was not built")
            return
        if not fleet_graph.passed(state, sid):
            add(False, f"{sid}'s latest build was not reviewed PASS")
            return
        if split:
            add(True, f"{sid} reviewed PASS as a whole, split into {', '.join(c['id'] for c in split['slices'])}")
            for child in split["slices"]:
                built_and_passed(child)
            return
        head = (built.get("git") or {}).get("head")
        if not in_git:
            add(True, f"{sid} built and reviewed PASS (not in git: nothing to check there)")
            return
        if not head:
            add(False, f"{sid}: where its builder ended is not known")
            return
        on_branch = _git(own["path"], "merge-base", "--is-ancestor", head, state["branch"])
        base = (built.get("git") or {}).get("base")
        add(on_branch is not None and on_branch.returncode == 0,
            f"{sid} built, reviewed PASS, and its commits are on {state['branch']}"
            if on_branch is not None and on_branch.returncode == 0
            else f"{sid}'s commits ({head[:10]}) are not on {state['branch']}")
        if base and head == base:
            add(False, f"{sid}'s builder made no commits")

    for s in plan["slices"]:
        built_and_passed(s)
        record = state.get("slice_worktrees", {}).get(s["id"])
        if record and in_git:
            dirty = _clean(record["path"])
            add(not dirty, f"{s['id']}'s worktree is clean" if not dirty else f"{s['id']}'s worktree {dirty}")
    if state.get("shape") == "diamond":
        integrator = fleet.latest(state, "integrator", done=True)
        if not integrator:
            add(False, "the integrator has not finished")
        else:
            result = integrator["result"]
            missing = [s["id"] for s in plan["slices"] if s["id"] not in result["merged"]]
            add(not missing, "the integrator merged every slice" if not missing
                else f"the integrator did not merge {', '.join(missing)}")
            add(result["suite"]["passed"], f"the full suite passed ({result['suite']['command']})"
                if result["suite"]["passed"] else "the full suite failed")
    if in_git:
        dirty = _clean(own["path"])
        add(not dirty, "the run's worktree is clean" if not dirty else f"the run's worktree {dirty}")
        count = _git(own["path"], "rev-list", "--count", f"{own['base']}..{state['branch']}")
        made = int(count.stdout.strip() or 0) if count is not None and count.returncode == 0 else 0
        add(made, f"{state['branch']} has {made} commit{'s' if made != 1 else ''} to merge" if made
            else f"{state['branch']} has no commits")
        files = fleet_scope.branch_changed(own["path"], own["base"], state["branch"])
        allowed = [f for s in plan["slices"] for f in s["files"]] + fleet_scope.accepted(state)
        extra = fleet_scope.outside(files or [], allowed)
        if files is None:
            add(False, f"git cannot list what {state['branch']} changed")
        else:
            add(not extra, "every file it changed is in a slice, or accepted" if not extra
                else "files changed outside every slice: " + ", ".join(extra[:8])
                + (f" and {len(extra) - 8} more" if len(extra) > 8 else ""))
    return checks


def close(state, now=None):
    """Close a run that passes every check: its slices' worktrees go, its
    own stays for you to merge. Returns (checks, notes); raises RunError
    (with the failed checks) when it may not close."""
    checks = check(state)
    failed = [what for ok, what in checks if not ok]
    if failed:
        raise fleet.RunError("it cannot be closed yet: " + "; ".join(failed))
    notes = []
    for sid, record in (state.get("slice_worktrees") or {}).items():
        try:
            notes.append(f"{sid}: " + worktrees.remove(worktrees.find(record["path"])))
        except worktrees.WorktreeError as e:
            notes.append(f"{sid}: kept its worktree ({e})")
    if state.get("branch"):
        notes.append(f"its work is on {state['branch']}; merge it with `omaorchestra worktree merge "
                     f"{state['branch']}`")
    state["closed"] = {"at": now or time.time(), "head": _head(state)}
    fleet.activity(state["id"], {"event": "closed"}, now)
    return checks, notes


def _head(state):
    own = state.get("worktree")
    if not own:
        return None
    result = _git(own["path"], "rev-parse", "HEAD")
    return result.stdout.strip() if result is not None and result.returncode == 0 else None
