"""The scope check: did a builder keep to the files you approved?

No hook blocks a builder while it works (hooks never block). Instead, when
a builder finishes, git says which files it changed since it started
(committed, uncommitted and new), and they are compared with its slice's
approved files. A file outside them holds the slice before review until
you accept it (with a reason, recorded on the builder) or send the slice
back to a new builder with the list. The reviewer is told about accepted
files, and closing a run repeats the comparison across the run's branch.

A slice's file may name a file, a folder (everything under it), or a glob
(`tests/*.py`).
"""

import fnmatch

from . import worktrees


def changed(cwd, base):
    """Files changed in `cwd` since commit `base`: committed, uncommitted
    and new (untracked, not ignored). None when git cannot say."""
    try:
        diff = worktrees.git(cwd, "diff", "--name-only", base, check=False)
        new = worktrees.git(cwd, "ls-files", "--others", "--exclude-standard", check=False)
    except worktrees.WorktreeError:
        return None
    if diff.returncode != 0 or new.returncode != 0:
        return None
    return sorted({f for f in (diff.stdout + new.stdout).splitlines() if f.strip()})


def branch_changed(cwd, base, branch):
    """Files that differ between `base` and `branch`; None when git cannot say."""
    try:
        diff = worktrees.git(cwd, "diff", "--name-only", f"{base}..{branch}", check=False)
    except worktrees.WorktreeError:
        return None
    return sorted({f for f in diff.stdout.splitlines() if f.strip()}) if diff.returncode == 0 else None


def covers(pattern, path):
    pattern = pattern.strip().removeprefix("./").rstrip("/")
    return (path == pattern or path.startswith(pattern + "/")
            or (any(c in pattern for c in "*?[") and fnmatch.fnmatch(path, pattern)))


def outside(files, allowed):
    """The files no allowed pattern covers."""
    return [f for f in files if not any(covers(p, f) for p in allowed)]


def accepted(state, slice_id=None):
    """Files you accepted outside a slice (every slice's, without one)."""
    out = []
    for n in state["nodes"].values():
        scope = n.get("scope") or {}
        if n["role"] == "builder" and scope.get("accepted") and (slice_id is None or n.get("slice") == slice_id):
            out += scope["extra"]
    return out
