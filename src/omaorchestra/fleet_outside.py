"""Fleet runs from elsewhere, read-only: graph_agents runs and Claude Code
agent teams, in the shape omafleet draws (fleet.py's), so the same list,
graph, board and timeline show them. Nothing here writes to them: no gate
is answered, no state changed; they are for watching.

  graph_agents  <folder>/.graph/runs/<run>/state.json and activity.jsonl,
                for each folder in [fleets] watch (a graph_agents checkout,
                or the folder that holds one). Read as a convention, not an
                import (graph_agents' own rule).
  agent teams   ~/.claude/teams/<team>/config.json (its members) and
                ~/.claude/tasks/<team>/ (its task list), while Claude Code
                keeps them ([fleets] agent_teams).

Their files are other programs': a field missing, of another type, or still
the schema's placeholder text ("pending|done|failed") is read as unknown,
and a run that cannot be read at all is left out, never guessed at.
"""

import json
import os
import time
from pathlib import Path

from . import config

GRAPH_AGENTS_STATUS = {"scouting": "running", "planning": "running", "building": "running",
                       "reviewing": "running", "integrating": "running", "awaiting-approval": "at-gate",
                       "done": "done", "blocked": "held", "parked": "held"}
ROLES = ("scout", "architect", "builder", "reviewer", "integrator")


def _text(value, limit=2000):
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _known(value):
    """A value, unless it is missing or still the schema's placeholder."""
    return value if isinstance(value, str) and value and "|" not in value else None


def _mtime(*paths):
    times = []
    for p in paths:
        try:
            times.append(os.stat(p).st_mtime)
        except OSError:
            continue
    return max(times) if times else time.time()


def _node(nid, role, slice_id=None, attempt=1, status="done", result=None, git=None):
    return {"id": nid, "role": role, "slice": slice_id, "attempt": attempt, "status": status, "session": None,
            "agent": "claude", "started": None, "ended": None, "result": result, "written_by": role if result else None,
            "error": None, "feedback": [], "git": git}


# ---------------------------------------------------------------- graph_agents

def run_dirs(folders):
    """The .graph/runs folders under the watched folders."""
    found = []
    for folder in folders or []:
        base = Path(folder).expanduser()
        for candidate in (base / ".graph" / "runs", base / "graph_agents" / ".graph" / "runs"):
            if candidate.is_dir() and candidate not in found:
                found.append(candidate)
    return found


def _verdicts(review):
    """[(attempt, verdict, review dict)] in order: the top level is attempt 1,
    then attempt_2, attempt_3... A placeholder verdict is left out."""
    if not isinstance(review, dict):
        return []
    out = []
    if _known(review.get("verdict")) in ("PASS", "REJECT"):
        out.append((1, review["verdict"], review))
    n = 2
    while isinstance(review.get(f"attempt_{n}"), dict):
        attempt = review[f"attempt_{n}"]
        if _known(attempt.get("verdict")) in ("PASS", "REJECT"):
            out.append((n, attempt["verdict"], attempt))
        n += 1
    return out


def _findings(items):
    out = []
    for f in items if isinstance(items, list) else []:
        if isinstance(f, dict):
            severity = "blocker" if str(f.get("severity", "")).lower() in ("blocker", "high", "critical") else "note"
            out.append({"severity": severity, "where": _text(f.get("where") or f.get("file") or "", 300),
                        "what": _text(f.get("what") or f.get("finding") or f.get("issue") or f, 1000)})
        elif isinstance(f, str):
            out.append({"severity": "note", "where": "", "what": _text(f, 1000)})
    return out


def graph_agents_run(state_file):
    """A graph_agents run as a fleet state, or None when it cannot be read."""
    state_file = Path(state_file)
    try:
        s = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict):
        return None
    run_id = _known(s.get("run_id")) or state_file.parent.name
    architect = s.get("architect") if isinstance(s.get("architect"), dict) else {}
    slices = []
    for p in architect.get("plan") if isinstance(architect.get("plan"), list) else []:
        if isinstance(p, dict) and _known(p.get("slice")):
            files = p.get("files") if isinstance(p.get("files"), list) else []
            slices.append({"id": p["slice"], "intent": _text(p.get("intent"), 400), "files": [str(f) for f in files],
                           "done_when": _text(p.get("done_when"), 400),
                           "risk": p.get("risk") if p.get("risk") in ("high", "low") else "low",
                           "risk_why": _text(p.get("risk_why"), 200)})
    nodes = {}
    # Runs from before graph_agents stamped `written_by` (2026-08-26) have
    # none: a part counts once it has content of its own.
    scout = s.get("scout")
    if isinstance(scout, dict) and (_known(scout.get("written_by")) or scout.get("facts")):
        facts = [{"fact": _text(f, 600), "where": ""} for f in scout.get("facts") or [] if f]
        nodes["scout"] = _node("scout", "scout", result={
            "facts": facts, "unknowns": [_text(u, 600) for u in scout.get("unknowns") or []],
            "risks": [_text(r, 600) for r in scout.get("risks") or []], "build": "not run", "plan_killer": None})
    if _known(architect.get("written_by")) or slices or architect.get("shape") in ("single-loop", "diamond"):
        not_doing = architect.get("not_doing")
        nodes["architect"] = _node("architect", "architect", result={
            "shape": architect.get("shape") if architect.get("shape") in ("single-loop", "diamond") else "single-loop",
            "rationale": _text(architect.get("rationale"), 2000), "slices": slices, "edges": [],
            "not_doing": [_text(x, 400) for x in not_doing] if isinstance(not_doing, list) else
            ([_text(not_doing, 800)] if not_doing else []),
            "approve": _text(architect.get("human_gate"), 800)})
    run_status = GRAPH_AGENTS_STATUS.get(s.get("status"), "running")
    builders = s.get("builders") if isinstance(s.get("builders"), dict) else {}
    for sid, b in builders.items():
        if not isinstance(b, dict):
            continue
        status = _known(b.get("status"))
        node_status = {"done": "done", "failed": "failed"}.get(status, "done" if run_status == "done" else "running")
        result = None
        if node_status == "done":
            changed = b.get("changed") if isinstance(b.get("changed"), list) else []
            result = {"status": "done", "changed": [str(c) for c in changed],
                      "done_when": {"command": "", "output": _text(b.get("gate_results"), 2000), "passed": True},
                      "blocked": None, "noticed": _text(b.get("notes"), 400) or None}
        nodes[f"builder.{sid}"] = _node(f"builder.{sid}", "builder", sid, status=node_status, result=result,
                                        git={"branch": _known(b.get("branch"))} if _known(b.get("branch")) else None)
    reviews = s.get("reviews") if isinstance(s.get("reviews"), dict) else {}
    for sid, review in reviews.items():
        for attempt, verdict, r in _verdicts(review):
            nid = f"reviewer.{sid}" + (f".{attempt}" if attempt > 1 else "")
            nodes[nid] = _node(nid, "reviewer", sid, attempt, result={
                "verdict": verdict, "findings": _findings(r.get("findings")), "summary": _text(r.get("summary"), 1200),
                "reran": ""})
    integrator = s.get("integrator")
    merged = (integrator or {}).get("merged") if isinstance(integrator, dict) else None
    merged = [m for m in merged if isinstance(m, dict) and m.get("slice")] if isinstance(merged, list) else []
    if isinstance(integrator, dict) and (_known(integrator.get("written_by")) or merged):
        verification = integrator.get("verification") if isinstance(integrator.get("verification"), dict) else {}
        nodes["integrator"] = _node("integrator", "integrator", result={
            "merged": [str(m["slice"]) for m in merged],
            "conflicts": [], "suite": {"command": _text(verification.get("command"), 400),
                                       "output": _text(verification.get("output"), 2000), "passed": True},
            "blocked": None, "escalate": None})
    activity_file = state_file.parent / "activity.jsonl"
    events = activity(activity_file)
    created = min((e["at"] for e in events), default=_mtime(state_file))
    return {
        "v": 1, "id": f"graph_agents:{run_id}", "outside": "graph_agents", "source": str(state_file.parent),
        "goal": _text(s.get("goal"), 400) or run_id, "folder": str(state_file.parent), "fleet": "graph_agents",
        "template": {"name": "graph_agents", "shape": "auto", "scout": "scout" in nodes or True, "tries": 2,
                     "budget": 0, "max_steps": 999, "stall_minutes": 20, "roles": {r: r for r in ROLES}},
        "repo": True, "status": run_status, "gate": "plan" if run_status == "at-gate" else None,
        "reason": _text(s.get("parked_note") or ("parked" if s.get("status") == "parked" else
                                                "blocked" if s.get("status") == "blocked" else ""), 400) or None,
        "held_by": None, "since": None, "approved": {"plan": {"at": None}} if s.get("approved_by_human") is True else {},
        "shape": architect.get("shape") if architect.get("shape") in ("single-loop", "diamond") else None,
        "shape_note": None, "worktree": None, "branch": None, "slice_worktrees": {}, "limits": {},
        "created": created, "updated": _mtime(state_file, activity_file), "nodes": nodes,
    }


def activity(activity_file):
    """graph_agents' activity.jsonl as fleet events: a lane per agent it
    started (its type and id), from its start to its stop. Tool calls are
    not lanes; a stop with no start (its log has many) is left out."""
    events, started = [], set()
    try:
        lines = Path(activity_file).read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or not isinstance(e.get("t"), (int, float)):
            continue
        agent, agent_id = e.get("agent"), str(e.get("id") or "")
        lane = f"{agent} {agent_id[:5]}".strip() if agent_id else None
        if e.get("ev") == "start" and lane:
            started.add(lane)
            events.append({"at": e["t"], "event": "started", "node": lane})
        elif e.get("ev") == "stop" and lane in started:
            events.append({"at": e["t"], "event": "ended", "node": lane})
    return events


# ---------------------------------------------------------------- agent teams

def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def team_runs(root=None):
    """Claude Code agent teams while they run (their config exists), as
    fleet states: a node per member, and its task list."""
    root = Path(root) if root else claude_dir()
    out = []
    for cfg in sorted((root / "teams").glob("*/config.json")) if (root / "teams").is_dir() else []:
        try:
            data = json.loads(cfg.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        name = cfg.parent.name
        members = [m for m in data.get("members") or [] if isinstance(m, dict)]
        nodes = {}
        for m in members:
            member = _text(m.get("name") or m.get("agentId") or "member", 80)
            nodes[member] = _node(member, _text(m.get("agentType") or "teammate", 60), status="running")
        tasks = []
        task_dir = root / "tasks" / name
        for task_file in sorted(task_dir.glob("*.json")) if task_dir.is_dir() else []:
            try:
                task = json.loads(task_file.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(task, dict):
                blocked = task.get("blockedBy") or task.get("blocked_by") or []
                tasks.append({"id": _text(task.get("id") or task_file.stem, 40),
                              "subject": _text(task.get("subject") or task.get("title") or task.get("description"), 300),
                              "status": _text(task.get("status") or "pending", 30),
                              "owner": _text(task.get("owner"), 80),
                              "blockedBy": [str(b) for b in blocked] if isinstance(blocked, list) else []})
        out.append({
            "v": 1, "id": f"team:{name}", "outside": "agent-team", "source": str(cfg.parent),
            "goal": f"Claude agent team {name}", "folder": str(cfg.parent), "fleet": "agent team",
            "template": {"name": "agent team", "shape": "auto", "scout": False, "tries": 0, "roles": {}},
            "repo": False, "status": "running", "gate": None, "reason": None, "held_by": None, "since": None,
            "approved": {}, "shape": None, "shape_note": None, "worktree": None, "branch": None, "slice_worktrees": {},
            "limits": {}, "created": _mtime(cfg), "updated": _mtime(cfg, task_dir), "nodes": nodes, "tasks": tasks,
        })
    return out


# ---------------------------------------------------------------- all of them

def runs(settings=None):
    """Every outside run that can be read, from the watched folders and
    (when on) the agent teams."""
    settings = settings or config.load_or_defaults()["fleets"]
    found = []
    for runs_dir in run_dirs(settings.get("watch")):
        for state_file in sorted(runs_dir.glob("*/state.json")):
            run = graph_agents_run(state_file)
            if run:
                found.append(run)
    if settings.get("agent_teams", True):
        found += team_runs()
    return sorted(found, key=lambda r: r["updated"], reverse=True)


def find(run_id, settings=None):
    """(state, events) of an outside run by id or prefix; KeyError if none."""
    matches = [r for r in runs(settings) if r["id"].startswith(run_id)]
    exact = [r for r in matches if r["id"] == run_id]
    if not exact and len(matches) != 1:
        raise KeyError(f"no outside run matching {run_id}" if not matches else
                       f"{run_id} matches {len(matches)} outside runs")
    state = (exact or matches)[0]
    events = activity(Path(state["source"]) / "activity.jsonl") if state["outside"] == "graph_agents" else []
    return state, events
