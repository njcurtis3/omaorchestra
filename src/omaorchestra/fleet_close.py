"""Closing a fleet run: checked against git, not against what the state says.

A run may be closed once it has finished and git agrees:

  - every slice was built (done, not blocked) and its latest build was
    reviewed PASS;
  - the commits each slice's builder ended at are on the run's branch (in a
    diamond, the integrator merged them there);
  - the run's branch has commits of its own, and its worktree and each
    slice's are clean (nothing left uncommitted);
  - a diamond's integrator merged every slice and its full suite passed.

Closing removes the slices' worktrees (their work is on the run's branch;
a worktree that would lose work is kept, and said so) and keeps the run's
own worktree and branch: merging that into your branch stays yours, with
`omaorchestra worktree merge`, as for any task's worktree.

Adapted from graph_agents' close-run (github.com/njcurtis3/graph_agents).
"""

import time
from pathlib import Path

from . import fleet, fleet_graph, worktrees


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
    result = _git(path, "status", "--porcelain")
    if result is None or result.returncode != 0:
        return f"git cannot read {path}"
    return "has uncommitted changes" if result.stdout.strip() else None


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
    for s in plan["slices"]:
        sid = s["id"]
        built = fleet.latest(state, "builder", sid)
        if not built or built["status"] != "done" or built["result"]["status"] != "done":
            add(False, f"{sid} was not built")
            continue
        if not fleet_graph.passed(state, sid):
            add(False, f"{sid}'s latest build was not reviewed PASS")
            continue
        head = (built.get("git") or {}).get("head")
        if not in_git:
            add(True, f"{sid} built and reviewed PASS (not in git: nothing to check there)")
            continue
        if not head:
            add(False, f"{sid}: where its builder ended is not known")
            continue
        on_branch = _git(own["path"], "merge-base", "--is-ancestor", head, state["branch"])
        base = (built.get("git") or {}).get("base")
        add(on_branch is not None and on_branch.returncode == 0,
            f"{sid} built, reviewed PASS, and its commits are on {state['branch']}"
            if on_branch is not None and on_branch.returncode == 0
            else f"{sid}'s commits ({head[:10]}) are not on {state['branch']}")
        if base and head == base:
            add(False, f"{sid}'s builder made no commits")
        record = state.get("slice_worktrees", {}).get(sid)
        if record:
            dirty = _clean(record["path"])
            add(not dirty, f"{sid}'s worktree is clean" if not dirty else f"{sid}'s worktree {dirty}")
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
