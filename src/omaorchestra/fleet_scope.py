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

Caches that running the tests leaves behind (`__pycache__/`, `.pytest_cache/`
...) are not changes when they are new and untracked, even in a repository
that does not ignore them; committed, they are checked like any file.
"""

import fnmatch

from . import worktrees

# Folders and files a builder makes just by running the tests or a linter.
CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis", ".tox", ".nox")
CACHE_FILES = ("*.pyc", "*.pyo", ".coverage", ".coverage.*")


def is_cache(path):
    """True for a file inside a tool cache folder, or a cache file."""
    parts = path.strip().rstrip("/").split("/")
    return any(p in CACHE_DIRS for p in parts) or any(fnmatch.fnmatch(parts[-1], g) for g in CACHE_FILES)


def changed(cwd, base):
    """Files changed in `cwd` since commit `base`: committed, uncommitted
    and new (untracked, not ignored, and not a cache). None when git cannot say."""
    try:
        diff = worktrees.git(cwd, "diff", "--name-only", base, check=False)
        new = worktrees.git(cwd, "ls-files", "--others", "--exclude-standard", check=False)
    except worktrees.WorktreeError:
        return None
    if diff.returncode != 0 or new.returncode != 0:
        return None
    untracked = [f for f in new.stdout.splitlines() if not is_cache(f)]
    return sorted({f for f in diff.stdout.splitlines() + untracked if f.strip()})


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
