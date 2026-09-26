"""Per-task git worktrees, so agents working in parallel do not collide.

A task's worktree lives outside the repository (nothing appears in its
`git status`), on a branch `omaorchestra/<task words>-<id>` started from the
commit the checkout was on. Worktrees are recorded in the state directory,
because they outlive their sessions: they hold the agent's work until it is
merged or removed, both of which are deliberate steps.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import paths

TIMEOUT = 30
MAX_DIFF_BYTES = 200 * 1024


class WorktreeError(Exception):
    pass


def git(cwd, *args, check=True):
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                                timeout=TIMEOUT, errors="replace")
    except FileNotFoundError as e:
        raise WorktreeError("git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise WorktreeError(f"git {args[0]} took too long") from e
    if check and result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()}")
    return result


def repo_root(cwd):
    """The top of the git checkout `cwd` is in, or None."""
    try:
        result = git(cwd, "rev-parse", "--show-toplevel", check=False)
    except WorktreeError:
        return None
    return Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None


def base_dir():
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(os.environ.get("OMAORCHESTRA_WORKTREES") or Path(base) / "omaorchestra" / "worktrees")


def slug(task, limit=40):
    words = re.findall(r"[a-z0-9]+", task.lower())
    text = "-".join(words)[:limit].strip("-")
    return text or "task"


# ---------------------------------------------------------------- records

def _records_path():
    return paths.state_dir() / "worktrees.json"


def records():
    try:
        data = json.loads(_records_path().read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("path")] if isinstance(data, list) else []


def _save(items):
    target = _records_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    os.replace(tmp, target)


def find(key):
    """A record by session id, branch, path, or a prefix of the session id."""
    matches = [r for r in records()
               if key in (r.get("session_id"), r.get("branch"), r.get("path"))
               or (r.get("session_id") or "").startswith(key)]
    if not matches:
        raise WorktreeError(f"no worktree matching {key}")
    if len(matches) > 1:
        raise WorktreeError(f"{key} matches {len(matches)} worktrees; be more specific")
    return matches[0]


def set_review(path, review):
    """Record a review of the worktree at `path` (chain.py): verdict, when,
    who, and the findings file."""
    items = records()
    for r in items:
        if r.get("path") == path:
            r["review"] = review
            _save(items)
            return r
    raise WorktreeError(f"no worktree at {path}")


# ---------------------------------------------------------------- lifecycle

def create(cwd, task, session_id):
    """Make a worktree for a task started in `cwd`; returns its record.

    Starts from the checkout's current commit. Uncommitted changes in the
    checkout are not carried over.
    """
    root = repo_root(cwd)
    if root is None:
        raise WorktreeError(f"{cwd} is not in a git repository")
    head = git(root, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode != 0:
        raise WorktreeError("the repository has no commits yet")
    base = head.stdout.strip()
    branch_now = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    name = f"{slug(task)}-{session_id[:6]}"
    branch = f"omaorchestra/{name}"
    path = base_dir() / root.name / name
    path.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-b", branch, str(path), base)
    # Start the agent in the same subfolder it was asked to work in.
    relative = Path(cwd).resolve().relative_to(root.resolve()) if Path(cwd).resolve() != root.resolve() else Path()
    record = {
        "session_id": session_id, "task": task, "repo": str(root), "path": str(path),
        "workdir": str(path / relative), "branch": branch, "base": base,
        "base_branch": branch_now or None, "created": time.time(),
    }
    _save(records() + [record])
    return record


def status(record):
    """How the worktree stands against its base: commits, uncommitted work,
    whether it still exists and whether its branch is merged."""
    path = Path(record["path"])
    info = {"exists": path.is_dir(), "commits": [], "dirty": False, "merged": False}
    if not info["exists"]:
        return info
    log = git(path, "log", "--format=%h %s", f"{record['base']}..HEAD", check=False).stdout.strip()
    info["commits"] = log.split("\n") if log else []
    info["dirty"] = bool(git(path, "status", "--porcelain", check=False).stdout.strip())
    target = record.get("base_branch") or record["base"]
    merged = git(record["repo"], "merge-base", "--is-ancestor", record["branch"], target, check=False)
    info["merged"] = merged.returncode == 0 and bool(info["commits"])
    return info


def changes(record, max_bytes=MAX_DIFF_BYTES):
    """Everything done in the worktree since its base: commits and
    uncommitted work together, in the same shape as changes.uncommitted()."""
    result = {"repo": True, "stat": "", "diff": "", "untracked": [], "truncated": False, "error": "",
              "commits": [], "worktree": True}
    path = record["path"]
    if not Path(path).is_dir():
        result["error"] = "the worktree has been removed"
        return result
    try:
        result["commits"] = status(record)["commits"]
        result["stat"] = git(path, "diff", record["base"], "--stat", check=False).stdout.rstrip()
        diff = git(path, "diff", record["base"], check=False).stdout
        if len(diff.encode()) > max_bytes:
            diff = diff.encode()[:max_bytes].decode(errors="ignore")
            result["truncated"] = True
        result["diff"] = diff
        result["untracked"] = git(path, "ls-files", "--others", "--exclude-standard", check=False).stdout.split("\n")[:-1]
    except WorktreeError as e:
        result["error"] = str(e)
    return result


def merge(record):
    """Merge the task's branch into the branch it started from, in the main
    checkout. Refuses unless that checkout is clean and on that branch, and
    undoes a merge that conflicts. Returns a one-line summary."""
    repo, branch = record["repo"], record["branch"]
    target = record.get("base_branch")
    if not target:
        raise WorktreeError("the task started from a detached HEAD; merge it by hand")
    info = status(record)
    if info["dirty"]:
        raise WorktreeError("the worktree has uncommitted changes; commit or discard them first")
    if not info["commits"]:
        raise WorktreeError("the task's branch has no commits to merge")
    current = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    if current != target:
        raise WorktreeError(f"{repo} is on {current or 'a detached HEAD'}, not {target}; switch to {target} first")
    if git(repo, "status", "--porcelain", "--untracked-files=no", check=False).stdout.strip():
        raise WorktreeError(f"{repo} has uncommitted changes; commit or stash them first")
    result = git(repo, "merge", "--no-ff", "--no-edit", branch, check=False)
    if result.returncode != 0:
        git(repo, "merge", "--abort", check=False)
        raise WorktreeError(f"merging {branch} into {target} conflicts; nothing was changed")
    return f"merged {branch} into {target} ({len(info['commits'])} commit(s))"


def remove(record, force=False):
    """Delete the worktree and, if merged, its branch. Without `force`,
    refuses when that would lose work (uncommitted changes, unmerged commits)."""
    info = status(record)
    if info["exists"] and not force:
        if info["dirty"]:
            raise WorktreeError("the worktree has uncommitted changes; pass force to discard them")
        if info["commits"] and not info["merged"]:
            raise WorktreeError(f"{len(info['commits'])} commit(s) are not merged; merge first or pass force")
    if info["exists"]:
        git(record["repo"], "worktree", "remove", *(["--force"] if force else []), record["path"])
    else:
        git(record["repo"], "worktree", "prune", check=False)
    branch_deleted = git(record["repo"], "branch", "-D" if force else "-d", record["branch"], check=False).returncode == 0
    _save([r for r in records() if r.get("path") != record["path"]])
    return "removed the worktree" + (" and its branch" if branch_deleted else f"; kept branch {record['branch']}")
