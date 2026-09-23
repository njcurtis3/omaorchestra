"""Uncommitted git changes in a session's folder."""

import subprocess

MAX_DIFF_BYTES = 200 * 1024
TIMEOUT = 10


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          timeout=TIMEOUT, errors="replace")


def uncommitted(cwd, max_bytes=MAX_DIFF_BYTES):
    """Staged and unstaged changes against HEAD, plus untracked files.

    Returns {"repo", "stat", "diff", "untracked", "truncated", "error"}. These
    are all changes in the folder, not only the agent's: until tasks get their
    own worktrees there is no way to tell them apart.
    """
    result = {"repo": False, "stat": "", "diff": "", "untracked": [], "truncated": False, "error": ""}
    if not cwd:
        result["error"] = "the session has no folder"
        return result
    try:
        inside = git(cwd, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return result
        result["repo"] = True
        # A repository with no commits yet has no HEAD to diff against.
        base = "HEAD" if git(cwd, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0 else None
        diff_args = ["diff", base] if base else ["diff", "--cached"]
        result["stat"] = git(cwd, *diff_args, "--stat").stdout.rstrip()
        diff = git(cwd, *diff_args).stdout
        if len(diff.encode()) > max_bytes:
            diff = diff.encode()[:max_bytes].decode(errors="ignore")
            result["truncated"] = True
        result["diff"] = diff
        result["untracked"] = git(cwd, "ls-files", "--others", "--exclude-standard").stdout.split("\n")[:-1]
    except FileNotFoundError:
        result["error"] = "git is not installed"
    except subprocess.TimeoutExpired:
        result["error"] = "git took too long"
    except OSError as e:
        result["error"] = str(e)
    return result
