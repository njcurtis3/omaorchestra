"""Chained tasks: a queued task that waits for another to finish.

A child task (`queue add --after <id>`, a recipe's later steps, "Then…" in
the app) waits in the queue until its parent has run:

  finished  the parent's agent went idle after working, or its session ended
            as finished (history.outcome): the child is released, with a
            brief of what the parent did, and in the parent's worktree when
            it asked for the same one
  otherwise stopped, crashed, never started, dismissed, or its queued task
            was cancelled: the child is held, with the reason, and never
            skips ahead; resume or cancel it from the queue

A chain pauses by itself whenever a step waits for you (its parent is not
finished until it goes idle), queued steps obey the daily budget and usage
limits like any task, and nothing here merges or approves anything.

A review step (build-then-review, `worktree review`) is given the diff to
review in its prompt and asked not to use tools; when it finishes, its last
reply is saved next to the worktree as the review, with its verdict.
"""

import re
import time
from pathlib import Path

from . import handoff, history, transcript, worktrees

DIFF_LIMIT = 60_000
VERDICTS = {"ready": "ready", "approve": "ready", "approved": "ready", "lgtm": "ready",
            "needs work": "needs work", "changes": "needs work", "changes requested": "needs work"}


class ChainError(Exception):
    pass


def parent_brief(session):
    """What the step before did, for the next step's task."""
    lines = ["This task follows on from an earlier step" + (f" in {session['cwd']}" if session.get("cwd") else "")
             + ". What that step was doing when it finished:", ""]
    lines += handoff.context(session)
    lines.append("Check the current state of the files before you start.")
    return "\n".join(lines)


def worktree_of(session):
    """The worktree record a session ran in, or None."""
    path = session.get("worktree")
    if not path:
        return None
    try:
        return worktrees.find(path)
    except worktrees.WorktreeError:
        return None


def diff_for(session):
    """The work to review: the worktree's changes since its base, or, in a
    plain checkout, everything since the session started."""
    record = worktree_of(session)
    if record:
        changes = worktrees.changes(record, max_bytes=DIFF_LIMIT)
        if changes.get("error"):
            raise ChainError(changes["error"])
        commits = "\n".join(changes.get("commits") or [])
        return (f"Commits:\n{commits}\n\n" if commits else "") + (changes.get("diff") or ""), changes.get("truncated")
    cwd, start = session.get("cwd"), session.get("git_start")
    if not cwd or not start:
        raise ChainError("there is nothing to review: the step before did not run in a git repository")
    diff = history.git(cwd, "diff", start)
    if diff is None:
        raise ChainError("could not read the changes to review")
    return diff[:DIFF_LIMIT], len(diff) > DIFF_LIMIT


def review_task(task, diff, truncated):
    """The review step's prompt: the diff inline, and no tools needed."""
    return "\n".join([
        task.strip(), "",
        "Here are the changes to review" + (" (cut short: it was long)" if truncated else "") + ":",
        "```diff", diff.strip() or "(no changes)", "```", "",
        "Do not change any files and do not run any commands: everything you need is above. Answer with your "
        "findings, the most serious first, then end with a line that is exactly `Verdict: ready` or "
        "`Verdict: needs work`.",
    ])


def verdict(text):
    """"ready", "needs work", or "unclear", from a review's last `Verdict:` line."""
    for line in reversed(text.splitlines()):
        match = re.search(r"verdict\s*[:\-]\s*\**\s*([a-z ]+?)\s*\**\s*\.?\s*$", line.strip().strip("`"), re.I)
        if match:
            return VERDICTS.get(match.group(1).lower().strip(), "unclear")
    return "unclear"


def review_path(session):
    """Where a review is written: beside the worktree (never inside it, so it
    stays out of the branch), or beside the history for a plain checkout."""
    record = worktree_of(session)
    if record:
        return Path(record["path"] + ".review.md")
    return history.path().parent / "reviews" / f"{session['id']}.md"


def record_review(session, now=None):
    """Save a finished review step's findings; returns the review, or None
    when there is no reply to save."""
    text = transcript.last_reply(session.get("transcript_path")) if session.get("transcript_path") else ""
    if not text:
        return None
    target = review_path(session)
    target.parent.mkdir(parents=True, exist_ok=True)
    found = verdict(text)
    header = (f"# Review: {found}\n\nBy {session.get('agent') or 'an agent'}"
              + (f" ({session['model']})" if session.get("model") else "")
              + time.strftime(", %Y-%m-%d %H:%M", time.localtime(now or time.time())) + "\n\n")
    target.write_text(header + text + "\n")
    review = {"verdict": found, "at": now or time.time(), "file": str(target), "agent": session.get("agent"),
              "model": session.get("model")}
    record = worktree_of(session)
    if record:
        worktrees.set_review(record["path"], review)
    return review
