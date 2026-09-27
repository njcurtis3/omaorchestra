"""A fleet run's state: one folder per run, one part per node.

    $XDG_STATE_HOME/omaorchestra/fleets/<run-id>/state.json
    $XDG_STATE_HOME/omaorchestra/fleets/<run-id>/activity.jsonl

state.json holds the goal, the folder, the run's status, and its nodes
(scout, architect, builder.s1, reviewer.s1, builder.s1.2 for a second try,
integrator...). Each node records its role, slice, attempt, status, the
session that ran it, and its result: the JSON block its final reply ended
with (fleet_reply.py), checked and written here by the daemon, stamped
`written_by` with the node's role. A node never writes the state itself.

A reply that cannot be read or checked holds the node and the run, with the
reason; the same session going idle again (you asked it to fix its reply)
is read again. A node whose session stopped, crashed or never started
fails, and holds the run too. Nothing is guessed.

activity.jsonl is one line per event (a node started, waited for you,
worked, went idle, ended; the run held or released), for the timeline and
the postmortem.

Each node's task is its brief, derived from the state (never written by
hand, so it cannot drift from it), then its reply format.

Adapted from graph_agents' state.json and brief.py
(github.com/njcurtis3/graph_agents).
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import fleet_reply, history, paths, transcript

VERSION = 1
RUN_STATUSES = ("running", "at-gate", "held", "done", "cancelled")
NODE_STATUSES = ("waiting", "running", "done", "held", "failed", "cancelled")
ACTIVE = ("waiting", "running", "held")


class RunError(Exception):
    pass


def runs_dir():
    return paths.state_dir() / "fleets"


def _slug(text, limit=40):
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    slug = ""
    for word in words:
        if len(slug) + len(word) + 1 > limit:
            break
        slug = f"{slug}-{word}" if slug else word
    return slug or "run"


def new_id(goal, now=None):
    """<date>-<slug of the goal>, with -2, -3... when taken."""
    base = runs_dir()
    stem = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d") + "-" + _slug(goal)
    run_id, n = stem, 1
    while (base / run_id).exists():
        n += 1
        run_id = f"{stem}-{n}"
    return run_id


def create(goal, folder, template=None, now=None, path=None):
    """A new run of `template` (a fleet, fleet_graph.py; its settings are
    kept with the run, so editing fleets.toml later does not change it).
    `path` is the PATH its agents start with."""
    goal = " ".join(goal.split())
    if not goal:
        raise RunError("the goal is empty")
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise RunError(f"{folder} is not a directory")
    now = now or time.time()
    base = runs_dir()
    if not base.exists():
        paths.private_state_dir()  # the state folder, private, before the runs folder inside it
    base.mkdir(parents=True, exist_ok=True)
    run_id = new_id(goal, now)
    (base / run_id).mkdir(mode=0o700)
    from . import worktrees
    template = template or {"name": "auto", "shape": "auto", "scout": True, "tries": 2,
                            "roles": {stage: stage for stage in ("scout", "architect", "builder", "reviewer",
                                                                 "integrator")}}
    state = {"v": VERSION, "id": run_id, "goal": goal, "folder": str(folder), "fleet": template["name"],
             "template": template, "repo": worktrees.repo_root(folder) is not None, "path": path,
             "status": "running", "reason": None, "held_by": None, "gate": None, "approved": {},
             "shape": None, "shape_note": None, "worktree": None, "branch": None, "slice_worktrees": {},
             "created": now, "updated": now, "nodes": {}}
    save(state, now)
    activity(run_id, {"event": "created", "goal": goal}, now)
    return state


def _run_path(run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id or ""):
        raise RunError(f"not a run id: {run_id!r}")
    return runs_dir() / run_id


def save(state, now=None):
    """Write state.json whole, through a temporary file, so a reader never
    sees half of it."""
    state["updated"] = now or time.time()
    target = _run_path(state["id"]) / "state.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=1))
    os.replace(temporary, target)


def load(run_id):
    try:
        state = json.loads((_run_path(run_id) / "state.json").read_text())
    except FileNotFoundError:
        raise RunError(f"no run {run_id}") from None
    except (OSError, ValueError) as e:
        raise RunError(f"run {run_id} cannot be read: {e}") from None
    if not isinstance(state, dict) or state.get("v") != VERSION:
        raise RunError(f"run {run_id} is not in a format this version knows")
    return state


def runs():
    """Every readable run, newest first."""
    base = runs_dir()
    found = []
    for folder in base.glob("*/state.json") if base.is_dir() else ():
        try:
            found.append(load(folder.parent.name))
        except RunError:
            continue
    return sorted(found, key=lambda s: s.get("created") or 0, reverse=True)


def find(prefix):
    matches = [s for s in runs() if s["id"].startswith(prefix)]
    exact = [s for s in matches if s["id"] == prefix]
    if exact or len(matches) == 1:
        return (exact or matches)[0]
    if not matches:
        raise RunError(f"no run matching {prefix}")
    raise RunError(f"{prefix} matches {len(matches)} runs: {', '.join(s['id'] for s in matches[:5])}")


def activity(run_id, entry, now=None):
    line = json.dumps({"at": now or time.time(), **entry})
    with open(_run_path(run_id) / "activity.jsonl", "a") as f:
        f.write(line + "\n")


def read_activity(run_id):
    try:
        lines = (_run_path(run_id) / "activity.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- nodes

def node_id(role, slice_id=None, attempt=1):
    return role + (f".{slice_id}" if slice_id else "") + (f".{attempt}" if attempt > 1 else "")


def add_node(state, role, slice_id=None, feedback=None):
    """A new node waiting to start; a node with this role and slice already
    there makes this the next attempt."""
    attempt = 1 + sum(1 for n in state["nodes"].values() if n["role"] == role and n.get("slice") == slice_id)
    nid = node_id(role, slice_id, attempt)
    state["nodes"][nid] = {"id": nid, "role": role, "slice": slice_id, "attempt": attempt, "status": "waiting",
                           "session": None, "agent": None, "started": None, "ended": None, "result": None,
                           "written_by": None, "error": None, "feedback": list(feedback or [])}
    return state["nodes"][nid]


def node(state, nid):
    if nid not in state["nodes"]:
        raise RunError(f"run {state['id']} has no node {nid}")
    return state["nodes"][nid]


def latest(state, role, slice_id=None, done=False):
    """The last attempt of a role (for a slice), or None; with `done`, the
    last one that finished with a result."""
    found = [n for n in state["nodes"].values() if n["role"] == role and n.get("slice") == slice_id
             and (not done or n["status"] == "done")]
    return max(found, key=lambda n: n["attempt"]) if found else None


def for_session(state, session_id):
    return next((n for n in state["nodes"].values() if n.get("session") == session_id), None)


def plan(state):
    """The architect's result the run follows: the last one that finished."""
    architect = latest(state, "architect", done=True)
    return architect["result"] if architect else None


def slice_of(state, slice_id):
    return next((s for s in (plan(state) or {}).get("slices") or [] if s["id"] == slice_id), None)


def live_plan(state):
    """The plan the run carries out: the architect's, less the slices you
    dropped at the gate (the architect's own result is never changed)."""
    p = plan(state)
    if p is None:
        return None
    dropped = set(state.get("dropped") or [])
    return {**p, "slices": [s for s in p["slices"] if s["id"] not in dropped],
            "edges": [e for e in p.get("edges") or [] if e["from"] not in dropped and e["to"] not in dropped]}


def current(runs_found, now=None, days=7):
    """The runs worth showing: every one not over, and those that ended in
    the last `days`."""
    now = now or time.time()
    return [s for s in runs_found if s["status"] not in ("done", "cancelled") or now - s["updated"] < days * 86400]


def node_started(state, nid, session_id, agent, now=None):
    n = node(state, nid)
    now = now or time.time()
    n.update(status="running", session=session_id, agent=agent, started=now, error=None)
    activity(state["id"], {"event": "started", "node": nid, "session": session_id, "agent": agent}, now)
    if state.get("held_by") == nid:
        release(state, now)  # it could not start before; now it has
    return n


def hold(state, reason, by=None, now=None):
    state.update(status="held", reason=reason, held_by=by)
    activity(state["id"], {"event": "held", "node": by, "reason": reason}, now)


def release(state, now=None):
    """Clear a hold, back to running."""
    if state["status"] == "held":
        activity(state["id"], {"event": "released", "node": state.get("held_by")}, now)
        state.update(status="running", reason=None, held_by=None)


def record_reply(state, nid, text, now=None, git=None):
    """A node's session finished its work: check its reply and store the
    result, or hold the node and the run with the reason. Returns the node."""
    n = node(state, nid)
    now = now or time.time()
    try:
        result = fleet_reply.parse(n["role"], text)
    except fleet_reply.ReplyError as e:
        n.update(status="held", error=f"its reply: {e}", ended=now)
        activity(state["id"], {"event": "bad-reply", "node": nid, "error": str(e)}, now)
        hold(state, f"{nid}: {n['error']}", nid, now)
        return n
    n.update(status="done", result=result, written_by=n["role"], error=None, ended=now, git=git or None)
    activity(state["id"], {"event": "result", "node": nid}, now)
    if state.get("held_by") == nid:
        release(state, now)
    return n


def node_failed(state, nid, outcome, now=None):
    """A node's session ended without finishing (stopped, crashed, never
    started, dismissed): the node fails and the run holds."""
    n = node(state, nid)
    now = now or time.time()
    n.update(status="failed", error=f"its session {outcome}", ended=now)
    activity(state["id"], {"event": "failed", "node": nid, "outcome": outcome}, now)
    hold(state, f"{nid}: {n['error']}", nid, now)
    return n


def read_reply(session):
    """A finished node's final reply, from its agent's own record: Claude's
    and Codex's transcripts, opencode's `export`. "" when it cannot be read."""
    if session.get("agent") == "opencode":
        return opencode_reply(session.get("id"))
    path = session.get("transcript_path")
    return transcript.last_reply(path) if path else ""


def git_facts(session):
    """Where a node's work is: its branch, the commit it ended at, and the one
    it started from. Recorded by the daemon, not claimed by the node."""
    cwd = session.get("cwd")
    if not cwd or not Path(cwd).is_dir():
        return None
    branch = history.git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    head = history.git(cwd, "rev-parse", "HEAD")
    if not head:
        return None
    return {"branch": branch if branch != "HEAD" else None, "head": head, "base": session.get("git_start")}


def opencode_reply(session_id, run=subprocess.run):
    if not session_id:
        return ""
    try:
        out = run(["opencode", "export", session_id], capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout[out.stdout.find("{"):]) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    for message in reversed(data.get("messages") or [] if isinstance(data, dict) else []):
        info = message.get("info") or {} if isinstance(message, dict) else {}
        if info.get("role") != "assistant":
            continue
        text = "\n".join(p.get("text", "") for p in message.get("parts") or []
                         if isinstance(p, dict) and p.get("type") == "text")
        if text.strip():
            return text.strip()
    return ""


# ---------------------------------------------------------------- briefs

def _bullets(items, fmt=str):
    return [f"- {fmt(item)}" for item in items] or ["- (none)"]


def _dropped_part(state):
    dropped = [s for s in (plan(state) or {}).get("slices") or [] if s["id"] in (state.get("dropped") or [])]
    if not dropped:
        return []
    return ["## Dropped from the plan (not to be done)", ""] + [f"- {s['id']}: {s['intent']}" for s in dropped] + [""]


def _scout_part(state):
    scout = latest(state, "scout", done=True)
    if not scout:
        return []
    r = scout["result"]
    lines = ["## What the scout found", "", f"Build: {r['build']}."]
    if r.get("plan_killer"):
        lines.append(f"Plan-killer: {r['plan_killer']}")
    lines += ["", "Facts:"] + _bullets(r["facts"], lambda f: f"{f['fact']} ({f['where']})")
    lines += ["", "Unknowns:"] + _bullets(r["unknowns"]) + ["", "Risks:"] + _bullets(r["risks"])
    return lines + [""]


def _slice_part(s, heading="## Your slice"):
    return [heading, "", f"Slice {s['id']}: {s['intent']}", "", "Files you may touch:"] + _bullets(s["files"]) + [
        "", f"Done when: {s['done_when']}", f"Risk: {s['risk']} ({s['risk_why']})", ""]


def _builder_part(n):
    r = n["result"]
    lines = [f"Status: {r['status']}." + (f" Blocked: {r['blocked']}" if r.get("blocked") else ""),
             "Changed:"] + _bullets(r["changed"])
    lines += ["", f"Done when, as the builder ran it: `{r['done_when']['command']}`"
              f" ({'passed' if r['done_when']['passed'] else 'failed'})", "```", r["done_when"]["output"], "```"]
    if r.get("noticed"):
        lines.append(f"Noticed outside the slice: {r['noticed']}")
    git = n.get("git") or {}
    if git.get("branch"):
        lines.append(f"Its work is on branch {git['branch']}"
                     + (f" (at {git['head'][:10]}, from {git['base'][:10]})" if git.get("head") and git.get("base")
                        else "") + ".")
    return lines + [""]


def _review_part(n):
    r = n["result"]
    lines = [f"Attempt {n['attempt']}: {r['verdict']}. {r['summary']}"]
    for f in r["findings"]:
        lines.append(f"- {f['severity']}: {f['where']}: {f['what']}"
                     + (f" (missed by the {f['origin']})" if f.get("origin") else ""))
    return lines + [""]


def brief(state, nid):
    """What a node is told about the run: its goal, and what the nodes
    before it produced that it needs."""
    n = node(state, nid)
    role, slice_id = n["role"], n.get("slice")
    lines = [f"# {role.capitalize()}" + (f", slice {slice_id}" if slice_id else "")
             + (f", attempt {n['attempt']}" if n["attempt"] > 1 else ""), "",
             f"You are the {role} in a fleet run of omaorchestra, in {state['folder']}.", "",
             "## Goal", "", state["goal"], ""]
    if role == "scout":
        lines += ["You are the first step: nothing has been established yet.", ""]
    elif role == "architect":
        forced = (state.get("template") or {}).get("shape", "auto")
        if forced != "auto":
            lines += [f"This run's fleet uses the {forced} shape whatever you choose; plan for it.", ""]
        lines += _scout_part(state)
        before = latest(state, "architect", done=True)
        if before and before["id"] != nid:
            lines += ["## Your plan before", "", "```json", json.dumps(before["result"], indent=1), "```", ""]
    elif role in ("builder", "reviewer"):
        s = slice_of(state, slice_id)
        if s is None:
            raise RunError(f"the plan has no slice {slice_id}")
        lines += _slice_part(s, "## Your slice" if role == "builder" else "## The slice under review")
        others = [o for o in live_plan(state)["slices"] if o["id"] != slice_id]
        if others:
            lines += ["## The other slices (not yours)", ""]
            lines += [f"- {o['id']}: {o['intent']} ({', '.join(o['files'])})" for o in others] + [""]
        lines += _dropped_part(state)
        lines += _scout_part(state)
        reviews = sorted((r for r in state["nodes"].values() if r["role"] == "reviewer"
                          and r.get("slice") == slice_id and r["status"] == "done"), key=lambda r: r["attempt"])
        if role == "builder" and reviews and reviews[-1]["result"]["verdict"] == "REJECT":
            lines += ["## It was sent back", "", "Fix exactly these findings from the last review:", ""]
            lines += _review_part(reviews[-1])
        if role == "reviewer":
            built = latest(state, "builder", slice_id, done=True)
            if built:
                lines += ["## What the builder reports", ""] + _builder_part(built)
            if reviews:
                lines += ["## Earlier reviews of this slice", ""]
                for r in reviews:
                    lines += _review_part(r)
    elif role == "integrator":
        lines += ["## The slices", ""]
        for s in (live_plan(state) or {}).get("slices") or []:
            built = latest(state, "builder", s["id"], done=True)
            review = latest(state, "reviewer", s["id"], done=True)
            branch = ((built or {}).get("git") or {}).get("branch")
            verdict = review["result"]["verdict"] if review else "not reviewed"
            lines.append(f"- {s['id']}: {s['intent']}; branch {branch or '(none)'}; latest review: {verdict}")
        if state.get("branch"):
            lines += ["", f"Merge the slices that passed into branch {state['branch']}."]
        lines.append("")
        lines += _dropped_part(state)
    else:
        done = [d for d in state["nodes"].values() if d["status"] == "done" and d["id"] != nid]
        if done:
            lines += ["## What was done before", ""]
            lines += [f"- {d['id']}: " + json.dumps(d["result"])[:600] for d in done] + [""]
    if n.get("feedback"):
        lines += ["## From the person running this", ""] + _bullets(n["feedback"]) + [""]
    return "\n".join(lines).rstrip() + "\n"


def task_for(state, nid):
    """A node's whole task: its brief, then the reply format for its role."""
    return brief(state, nid) + "\n" + fleet_reply.reply_format(node(state, nid)["role"]) + "\n"
