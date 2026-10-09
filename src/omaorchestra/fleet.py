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
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import fleet_reply, fleet_scope, history, paths, transcript

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


def create(goal, folder, template=None, now=None, path=None, budget=None):
    """A new run of `template` (a fleet, fleet_graph.py; its settings are
    kept with the run, so editing fleets.toml later does not change it).
    `path` is the PATH its agents start with; `budget` (US$) replaces the
    fleet's."""
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
    template = template or {"name": "auto", "shape": "auto", "scout": True, "tries": 2, "budget": 0,
                            "max_steps": 30, "stall_minutes": 20, "max_depth": 1, "split_gate": False,
                            "roles": {stage: stage for stage in ("scout", "architect", "builder", "reviewer",
                                                                 "integrator")}}
    state = {"v": VERSION, "id": run_id, "goal": goal, "folder": str(folder), "fleet": template["name"],
             "template": template, "repo": worktrees.repo_root(folder) is not None, "path": path,
             "status": "running", "reason": None, "held_by": None, "gate": None, "approved": {},
             "shape": None, "shape_note": None, "worktree": None, "branch": None, "slice_worktrees": {},
             "limits": {"budget": budget if budget is not None else template.get("budget", 0),
                        "max_steps": template.get("max_steps", 30)},
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
    """A slice of the plan, or one a builder split its slice into."""
    found = next((s for s in (plan(state) or {}).get("slices") or [] if s["id"] == slice_id), None)
    if found or not slice_id:
        return found
    for n in state["nodes"].values():
        found = next((s for s in (split_plan(n) or {}).get("slices", []) if s["id"] == slice_id), None)
        if found:
            return found
    return None


def split_plan(n):
    """The smaller slices a builder split its slice into, or None. Their ids
    are made from the slice and the attempt (s2-a, s2-b; s2-2a for a second
    build's split), so a later split never reuses an earlier one's nodes."""
    result = n.get("result") or {}
    if n["role"] != "builder" or n["status"] != "done" or result.get("status") != "split":
        return None
    prefix = f"{n['slice']}-" + (str(n["attempt"]) if n["attempt"] > 1 else "")
    ids = {s["id"]: prefix + "abcdefgh"[i] for i, s in enumerate(result["split"]["slices"])}
    return {"rationale": result["split"]["rationale"],
            "slices": [{**s, "id": ids[s["id"]], "parent": n["slice"]} for s in result["split"]["slices"]],
            "edges": [{**e, "from": ids[e["from"]], "to": ids[e["to"]]} for e in result["split"]["edges"]]}


def sub_plan(state, slice_id):
    """The split the slice's latest build made, or None."""
    built = latest(state, "builder", slice_id)
    return split_plan(built) if built else None


def depth(state, slice_id):
    """0 for a slice of the plan, 1 for one split from it, and so on."""
    s = slice_of(state, slice_id)
    return 1 + depth(state, s["parent"]) if s and s.get("parent") else 0


def root_slice(state, slice_id):
    """The slice of the plan a split slice came from (itself, for one of the plan)."""
    s = slice_of(state, slice_id)
    return root_slice(state, s["parent"]) if s and s.get("parent") else slice_id


def can_split(state, slice_id):
    return depth(state, slice_id) + 1 < (state.get("template") or {}).get("max_depth", 1)


def split_problem(state, n, result, changed):
    """Why a builder's split cannot be taken, or None: it went too deep,
    reached past its slice's files, or changed something."""
    slice_id = n.get("slice")
    if not can_split(state, slice_id):
        limit = (state.get("template") or {}).get("max_depth", 1)
        return (f"slice {slice_id} cannot be split: this run's fleet allows "
                + ("no splits" if limit <= 1 else f"splits {limit - 1} level{'s' if limit != 2 else ''} deep"))
    s = slice_of(state, slice_id)
    allowed = (s["files"] if s else []) + fleet_scope.accepted(state, slice_id)
    for child in result["split"]["slices"]:
        extra = fleet_scope.outside([f.strip().removeprefix("./") for f in child["files"]], allowed)
        if extra:
            return f"split: slice {child['id']} reaches outside slice {slice_id}'s files: {', '.join(extra)}"
    if changed:
        return "a split must change nothing, but it changed: " + ", ".join(changed[:8]) + \
            (f" and {len(changed) - 8} more" if len(changed) > 8 else "")
    return None


def live_plan(state):
    """The plan the run carries out: the architect's, less the slices you
    dropped at the gate (the architect's own result is never changed)."""
    p = plan(state)
    if p is None:
        return None
    dropped = set(state.get("dropped") or [])
    return {**p, "slices": [s for s in p["slices"] if s["id"] not in dropped],
            "edges": [e for e in p.get("edges") or [] if e["from"] not in dropped and e["to"] not in dropped]}


def needs_you(state):
    """At a gate, held, or a node's session waiting for you."""
    return state["status"] in ("at-gate", "held") or any(
        n.get("waiting") and n["status"] == "running" for n in state["nodes"].values())


def summary_path():
    return paths.state_dir() / "fleets.json"


def write_summary():
    """fleets.json beside sessions.json: the runs not over, in brief, for the
    bar widget (which reads files, never the socket). Written whole, via a
    rename, like the registry."""
    runs_now = [s for s in current(runs()) if s["status"] != "cancelled" and not s.get("closed")]
    out = [{"id": s["id"], "goal": s["goal"], "folder": s.get("folder"), "status": s["status"],
            "gate": s.get("gate"), "reason": s.get("reason"), "needsYou": needs_you(s), "updated": s["updated"],
            "nodes": len(s["nodes"]), "done": sum(1 for n in s["nodes"].values() if n["status"] == "done")}
           for s in runs_now]
    target = summary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(out))
    os.replace(temporary, target)
    return out


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
    state.update(status="held", reason=reason, held_by=by, since=now or time.time())
    activity(state["id"], {"event": "held", "node": by, "reason": reason}, now)


def release(state, now=None):
    """Clear a hold, back to running."""
    if state["status"] == "held":
        activity(state["id"], {"event": "released", "node": state.get("held_by")}, now)
        state.update(status="running", reason=None, held_by=None, since=None)


def spent(state):
    """What the run's finished nodes cost (US$; API-equivalent for
    subscription sessions)."""
    return round(sum((n.get("cost") or {}).get("usd") or 0 for n in state["nodes"].values()), 4)


def record_reply(state, nid, text, now=None, git=None, changed=None, cost=None):
    """A node's session finished its work: check its reply and store the
    result, or hold the node and the run with the reason. Returns the node.
    `changed` (a builder's files, from git) is checked against its slice;
    `cost` is what its session cost."""
    n = node(state, nid)
    now = now or time.time()
    if cost:
        n["cost"] = cost
    if n["role"] == "builder" and changed is not None:
        s = slice_of(state, n.get("slice"))
        allowed = (s["files"] if s else []) + fleet_scope.accepted(state, n.get("slice"))
        n["scope"] = {"changed": changed, "extra": fleet_scope.outside(changed, allowed), "accepted": None}
    try:
        result = fleet_reply.parse(n["role"], text)
        if n["role"] == "builder" and result["status"] == "split":
            problem = split_problem(state, n, result, changed)
            if problem:
                raise fleet_reply.ReplyError(problem)
    except fleet_reply.ReplyError as e:
        said = str(e)
        n.update(status="held", error=said if said.startswith("its ") else f"its reply: {said}", error_kind="reply",
                 ended=now)
        activity(state["id"], {"event": "bad-reply", "node": nid, "error": str(e)}, now)
        hold(state, f"{nid}: {n['error']}", nid, now)
        return n
    n.update(status="done", result=result, written_by=n["role"], error=None, error_kind=None, ended=now,
             git=git or None)
    activity(state["id"], {"event": "result", "node": nid}, now)
    split = split_plan(n)
    if split:
        activity(state["id"], {"event": "split", "node": nid, "slices": [s["id"] for s in split["slices"]]}, now)
    if state.get("held_by") == nid:
        release(state, now)
    return n


def node_failed(state, nid, outcome, now=None, cost=None):
    """A node's session ended without finishing (stopped, crashed, never
    started, dismissed): the node fails and the run holds."""
    n = node(state, nid)
    now = now or time.time()
    if cost:
        n["cost"] = cost
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
        # Into a file: through a pipe, opencode exits before its export is
        # all written, and anything past 64 KB is lost (opencode 1.18).
        with tempfile.TemporaryFile("w+", encoding="utf-8") as f:
            out = run(["opencode", "export", session_id], stdout=f, stderr=subprocess.DEVNULL, timeout=30)
            f.seek(0)
            text = f.read()
        data = json.loads(text[text.find("{"):]) if out.returncode == 0 else {}
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


def board(state):
    """One row per slice of the plan the run carries out: its latest build
    and review, how many builds, the scope check, its branch and cost.
    What `fleet show`, `top` and the app draw. A slice a builder split is
    followed by its smaller slices (`parent`, one `depth` further in)."""
    rows = []

    def add(s, level):
        built = latest(state, "builder", s["id"])
        review = latest(state, "reviewer", s["id"])
        scope = (built or {}).get("scope") or {}
        nodes = [n for n in state["nodes"].values() if n.get("slice") == s["id"]]
        rows.append({
            "slice": s["id"], "intent": s["intent"], "risk": s["risk"], "parent": s.get("parent"), "depth": level,
            "node": (built or {}).get("id") or "",
            "build": "split" if built and split_plan(built) else (built or {}).get("status") or "not started",
            "blocked": ((built or {}).get("result") or {}).get("blocked"),
            "verdict": ((review or {}).get("result") or {}).get("verdict") if review and review["status"] == "done"
            else ((review or {}).get("status") if review else None),
            "tries": (built or {}).get("attempt") or 0,
            "extra": scope.get("extra") or [], "accepted": bool(scope.get("accepted")),
            "branch": ((state.get("slice_worktrees") or {}).get(root_slice(state, s["id"])) or {}).get("branch")
            or state.get("branch"),
            "cost": round(sum((n.get("cost") or {}).get("usd") or 0 for n in nodes), 4),
            "waiting": any(n.get("waiting") and n["status"] == "running" for n in nodes),
            "stalled": any(n.get("stalled") and n["status"] == "running" for n in nodes),
        })
        for child in (sub_plan(state, s["id"]) or {}).get("slices", []):
            add(child, level + 1)

    for s in (live_plan(state) or {}).get("slices") or []:
        add(s, 0)
    return rows


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


# Up front in a builder's brief when it may split: asked as a hint at the
# end of the reply format, builders took "split" for how to work, building
# the parts one after another themselves.
SPLIT_PART = [
    "## Build it, or split it", "",
    "Decide this before you change anything. Split the slice when it needs more than one reviewable change, or its "
    "files fall into parts that can be checked apart; otherwise build it.", "",
    "Splitting is a reply, not a way of working: you change nothing, and end with a `split` block (the reply format "
    "shows it) that lists smaller slices within your files. omaorchestra then starts a new builder and a new "
    "reviewer for each smaller slice, and a reviewer checks your slice as a whole at the end. Building the parts "
    "yourself, one after another, is not a split: that is building the slice, and you reply `done`.", ""]


# Told to the architect when builders may split: not knowing, it read a goal
# that said "split" as orders for the builder and wrote sub-steps into the
# slice ("built one at a time by the builder"), the opposite of a split.
ARCHITECT_SPLIT_PART = [
    "## Builders may split slices", "",
    "This run's fleet lets a builder split its slice into smaller ones: it hands them back, within the slice's "
    "files, and omaorchestra gives each its own builder and reviewer. So say what each slice must do and how to "
    "check it, never how to build it: no sub-steps or order of work for a builder. A slice may be bigger than one "
    "change; its builder decides whether to split it.", ""]


def _slice_part(s, heading="## Your slice"):
    return [heading, "", f"Slice {s['id']}: {s['intent']}", "", "Files you may touch:"] + _bullets(s["files"]) + [
        "", f"Done when: {s['done_when']}", f"Risk: {s['risk']} ({s['risk_why']})", ""]


def _split_part(state, n):
    """What a builder that split its slice made of it: each smaller slice,
    what its builds changed, and its latest review."""
    split = split_plan(n)
    lines = [f"It split the slice into {len(split['slices'])} smaller slices ({split['rationale']}), each built "
             "and reviewed in turn:", ""]
    for s in split["slices"]:
        review = latest(state, "reviewer", s["id"], done=True)
        changed = sorted({f for b in state["nodes"].values() if b["role"] == "builder" and b.get("slice") == s["id"]
                          and b["status"] == "done" for f in b["result"]["changed"]})
        lines.append(f"- {s['id']}: {s['intent']}; latest review: "
                     + (review["result"]["verdict"] if review else "not reviewed")
                     + (f"; changed {', '.join(changed)}" if changed else ""))
    return lines + [""]


def _builder_part(n):
    r = n["result"]
    lines = [f"Status: {r['status']}." + (f" Blocked: {r['blocked']}" if r.get("blocked") else ""),
             "Changed:"] + _bullets(r["changed"])
    lines += ["", f"Done when, as the builder ran it: `{r['done_when']['command']}`"
              f" ({'passed' if r['done_when']['passed'] else 'failed'})", "```", r["done_when"]["output"], "```"]
    if r.get("noticed"):
        lines.append(f"Noticed outside the slice: {r['noticed']}")
    scope = n.get("scope") or {}
    if scope.get("extra"):
        lines.append("Files it changed outside the slice: " + ", ".join(scope["extra"])
                     + (f" (accepted by the person running this: {scope['accepted']['reason']})"
                        if scope.get("accepted") else ""))
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
        if (state.get("template") or {}).get("max_depth", 1) > 1:
            lines += ARCHITECT_SPLIT_PART
        lines += _scout_part(state)
        before = latest(state, "architect", done=True)
        if before and before["id"] != nid:
            lines += ["## Your plan before", "", "```json", json.dumps(before["result"], indent=1), "```", ""]
    elif role in ("builder", "reviewer"):
        s = slice_of(state, slice_id)
        if s is None:
            raise RunError(f"the plan has no slice {slice_id}")
        lines += _slice_part(s, "## Your slice" if role == "builder" else "## The slice under review")
        if role == "builder" and can_split(state, slice_id):
            lines += SPLIT_PART
        if s.get("parent"):
            whole = slice_of(state, s["parent"])
            lines += ["## The slice it is part of", "", f"A builder split slice {whole['id']} ({whole['intent']}) into "
                      f"smaller slices; this is one of them. Done when, for the whole: {whole['done_when']}", ""]
        siblings = sub_plan(state, s["parent"])["slices"] if s.get("parent") else live_plan(state)["slices"]
        others = [o for o in siblings if o["id"] != slice_id]
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
            if built and split_plan(built):
                lines += ["## What the builders report", ""] + _split_part(state, built)
                lines += [f"Review slice {slice_id} as a whole: the smaller slices together must do what it says, "
                          "and its done-when must pass.", ""]
            elif built:
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
            lines += ["", f"You are on branch {state['branch']}, the run's own. Merge each slice that passed into it "
                          "(`git merge --no-ff <its branch>`), never into another branch."]
        lines += ["", "After merging, run the full test suite, and each slice's done-when again: they must still "
                      "pass together.", ""]
        lines += [f"- {s['id']}: {s['done_when']}" for s in (live_plan(state) or {}).get("slices") or []] + [""]
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
    n = node(state, nid)
    split = n["role"] == "builder" and can_split(state, n.get("slice"))
    return brief(state, nid) + "\n" + fleet_reply.reply_format(n["role"], can_split=split) + "\n"
