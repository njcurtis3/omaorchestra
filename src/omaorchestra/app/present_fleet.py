"""What the omafleet tab shows, from a run's state: plain data, no Qt, so it
is tested without a window (as the bar widget's logic lives in Format.js).

  row(state)        a run in the list: goal, group, a dot per node, spent
  cards(state)      the decisions it waits on you for, one card each
  graph(state)      the work graph laid out: columns by stage, a lane per slice
  timeline(...)     one lane per node over the run's time: working, waiting
  node_detail(...)  one node: what it was told, what it handed back
"""

import json
import time

from .. import fleet, fleet_graph
from . import present

GROUPS = ("needs-you", "running", "finished", "outside")
GROUP_LABEL = {"needs-you": "Needs you", "running": "Running", "finished": "Finished",
               "outside": "Outside (read-only)"}
OUTSIDE_LABEL = {"graph_agents": "a graph_agents run", "agent-team": "a Claude agent team"}
STATUS_WORD = {"running": "Running", "at-gate": "Waiting for you", "held": "Held", "done": "Finished",
               "cancelled": "Cancelled"}


def node_state(n):
    """What a node's dot or box shows: waiting to start, running, waiting for
    you, passed, rejected, failed, skipped, held."""
    if n["status"] == "running":
        return "you" if n.get("waiting") else "stalled" if n.get("stalled") else "running"
    if n["status"] == "done":
        result = n.get("result") or {}
        if result.get("verdict") == "REJECT" or result.get("status") == "blocked":
            return "rejected"
        return "passed"
    return {"waiting": "queued", "held": "held", "failed": "failed", "cancelled": "skipped"}.get(n["status"], "queued")


def group(state):
    if state.get("outside"):
        return "outside"  # not ours to answer
    if fleet.needs_you(state):
        return "needs-you"
    return "running" if state["status"] == "running" else "finished"


def status_text(state):
    if state.get("outside") == "agent-team":
        tasks = state.get("tasks") or []
        done = sum(1 for t in tasks if t.get("status") == "completed")
        return f"{len(state['nodes'])} teammate{'s' if len(state['nodes']) != 1 else ''}" + (
            f", {done} of {len(tasks)} tasks done" if tasks else "")
    if state["status"] == "at-gate":
        return f"Waiting at the {state.get('gate')} gate"
    waiting = [n["id"] for n in state["nodes"].values() if n.get("waiting") and n["status"] == "running"]
    stalled = [n["id"] for n in state["nodes"].values() if n.get("stalled") and n["status"] == "running"]
    if state["status"] == "running" and waiting:
        return f"Running: {', '.join(waiting)} waits for you"
    if state["status"] == "running" and stalled:
        return f"Running: {', '.join(stalled)} looks stalled"
    if state["status"] == "done" and state.get("closed"):
        return "Closed"
    return STATUS_WORD.get(state["status"], state["status"])


def row(state, now=None):
    now = now or time.time()
    limits = state.get("limits") or {}
    end = state.get("finished") or (state["updated"] if state["status"] in ("done", "cancelled") else now)
    return {
        "id": state["id"], "goal": state["goal"], "project": present.project(state.get("folder")),
        "place": present.place(state.get("folder")), "group": group(state), "groupLabel": GROUP_LABEL[group(state)],
        "status": state["status"], "statusText": status_text(state), "gate": state.get("gate") or "",
        "reason": state.get("reason") or "", "heldBy": state.get("held_by") or "",
        "folder": state.get("folder") or "", "shape": state.get("shape") or state.get("fleet") or "",
        "fleet": state.get("fleet") or "", "closed": bool(state.get("closed")),
        "dots": [{"id": n["id"], "state": node_state(n)} for n in state["nodes"].values()],
        "spent": fleet.spent(state), "budget": limits.get("budget") or 0,
        "elapsed": present.duration(end - state["created"]), "created": state["created"],
        "updated": state["updated"], "branch": state.get("branch") or "",
        "outside": state.get("outside") or "", "outsideLabel": OUTSIDE_LABEL.get(state.get("outside"), ""),
        "source": state.get("source") or "",
    }


def rows(states, now=None):
    """Runs for the list: needs-you first, then running, then finished;
    newest first within each."""
    out = [row(s, now) for s in states]
    return sorted(out, key=lambda r: (GROUPS.index(r["group"]), -r["updated"]))


# ---------------------------------------------------------------- decisions

def _latest_reviews(state, slice_id, count=2):
    done = sorted((n for n in state["nodes"].values() if n["role"] == "reviewer" and n.get("slice") == slice_id
                   and n["status"] == "done"), key=lambda n: n["attempt"])
    return [{"id": n["id"], "attempt": n["attempt"], "verdict": n["result"]["verdict"],
             "summary": n["result"]["summary"], "findings": n["result"]["findings"]} for n in done[-count:]]


def cards(state):
    """The decisions a run waits on you for. Each card: kind, title, what it
    says, and the actions it offers (the tab draws the buttons)."""
    out = []
    if state.get("outside"):
        return out  # watched, never answered from here
    since = state.get("since")
    if state["status"] == "at-gate" and state.get("gate") == "plan":
        plan = fleet.live_plan(state) or {}
        scout = fleet.latest(state, "scout", done=True)
        dropped = [s for s in (fleet.plan(state) or {}).get("slices") or [] if s["id"] in (state.get("dropped") or [])]
        out.append({
            "kind": "plan", "title": "Approve the plan", "since": since,
            "shape": state.get("shape"), "shapeNote": state.get("shape_note") or "", "proposed": plan.get("shape"),
            "rationale": plan.get("rationale") or "", "slices": plan.get("slices") or [],
            "edges": [f"{e['from']} before {e['to']}: {e['artifact']}" for e in plan.get("edges") or []],
            "notDoing": plan.get("not_doing") or [], "approve": plan.get("approve") or "",
            "risks": (scout or {}).get("result", {}).get("risks") if scout else [],
            "planKiller": ((scout or {}).get("result") or {}).get("plan_killer") or "",
            "dropped": [f"{s['id']}: {s['intent']}" for s in dropped],
            "actions": ["approve", "send-back", "drop", "shape", "cancel"],
        })
    elif state["status"] == "at-gate" and state.get("gate") == "merge":
        slices = [{"id": r["slice"], "intent": r["intent"], "branch": r["branch"]} for r in fleet.board(state)]
        out.append({"kind": "merge", "title": "Approve the merge", "since": since, "slices": slices,
                    "into": state.get("branch") or "", "actions": ["approve", "cancel"]})
    elif state["status"] == "held":
        held = state.get("held_by")
        n = state["nodes"].get(held) if held else None
        reason = state.get("reason") or ""
        if held == "paused":
            out.append({"kind": "paused", "title": "Paused by you", "since": since,
                        "text": "Nothing new starts; agents already working go on.", "actions": ["resume", "cancel"]})
        elif held == "limits":
            limits = state.get("limits") or {}
            out.append({"kind": "limits", "title": "At its limits", "since": since, "text": reason,
                        "budget": limits.get("budget") or 0, "steps": limits.get("max_steps") or 30,
                        "spent": fleet.spent(state), "used": len(state["nodes"]),
                        "actions": ["limits", "cancel"]})
        elif n and n["role"] == "builder" and (n.get("scope") or {}).get("extra") and not n["scope"].get("accepted"):
            out.append({"kind": "scope", "title": "Files outside the slice", "since": since, "text": reason,
                        "node": n["id"], "slice": n.get("slice"), "files": n["scope"]["extra"],
                        "session": n.get("session") or "", "actions": ["accept-files", "undo-files", "cancel"]})
        elif n and n.get("error_kind") == "reply":
            out.append({"kind": "bad-reply", "title": "A reply it could not read", "since": since, "text": reason,
                        "node": n["id"], "session": n.get("session") or "", "actions": ["retry", "cancel"]})
        elif held and fleet.slice_of(state, held) and "rejected" in reason:
            out.append({"kind": "rejected", "title": f"{held} rejected {state['template']['tries']} times",
                        "since": since, "text": reason, "slice": held, "reviews": _latest_reviews(state, held),
                        "session": (fleet.latest(state, "builder", held) or {}).get("session") or "",
                        "actions": ["retry", "take-over", "cancel"]})
        else:
            out.append({"kind": "held", "title": "Held", "since": since, "text": reason, "node": held or "",
                        "session": (n or {}).get("session") or "", "actions": ["retry", "cancel"]})
    elif state["status"] == "done" and not state.get("closed"):
        out.append({"kind": "close", "title": "Finished: check it and close it", "since": state.get("finished"),
                    "branch": state.get("branch") or "", "actions": ["close"]})
    return out


# ---------------------------------------------------------------- the graph

STAGE_COLUMN = {"scout": 0, "architect": 1, "plan-gate": 2, "builder": 3, "reviewer": 4, "merge-gate": 5,
                "integrator": 6}


def graph(state):
    """The run's graph laid out left to right: {"nodes": [...], "edges":
    [...], "columns", "lanes"}. A node: key, label, sub(text), state, col,
    lane, and node (the id of its latest attempt, "" before it has one). An
    edge: from, to, kind (flow; back: a REJECT sent it back; next: a single
    loop going on to its next slice), label. Gates are nodes too. Slices get a lane each; the stages around them sit
    in the middle lane."""
    plan = fleet.live_plan(state)
    slices = [s["id"] for s in (plan or {}).get("slices") or []]
    lanes = max(1, len(slices))
    middle = (lanes - 1) / 2
    diamond = state.get("shape") == "diamond"
    nodes, edges = [], []

    def add(key, label, sub, node_state_, col, lane, node=None):
        nodes.append({"key": key, "label": label, "sub": sub, "state": node_state_, "col": col, "lane": lane,
                      "node": (node or {}).get("id") or ""})

    def stage(role):
        n = fleet.latest(state, role)
        if n:
            add(role, role.capitalize(), (f"try {n['attempt']} · " if n["attempt"] > 1 else "") + (n.get("agent") or ""),
                node_state(n), STAGE_COLUMN[role], middle, n)
        return n

    if state["template"].get("scout", True):
        if not stage("scout"):
            add("scout", "Scout", "", "queued", 0, middle)
    if not stage("architect"):
        add("architect", "Architect", "", "queued", 1, middle)
    if state["template"].get("scout", True):
        edges.append({"from": "scout", "to": "architect", "kind": "flow", "label": ""})
    approved = "plan" in state.get("approved", {})
    gate_state = "passed" if approved else "you" if state.get("gate") == "plan" else "queued"
    add("plan-gate", "Plan gate", "you approve", gate_state, 2, middle)
    edges.append({"from": "architect", "to": "plan-gate", "kind": "flow", "label": ""})
    tries = state["template"].get("tries", 2)
    for lane, sid in enumerate(slices):
        built = fleet.latest(state, "builder", sid)
        review = fleet.latest(state, "reviewer", sid)
        rejects = sum(1 for n in state["nodes"].values() if n["role"] == "reviewer" and n.get("slice") == sid
                      and n["status"] == "done" and n["result"]["verdict"] == "REJECT")
        add(f"builder.{sid}", f"Builder {sid}", f"try {built['attempt']}/{tries}" if built and built["attempt"] > 1
            else (built or {}).get("agent") or "", node_state(built) if built else "queued", 3, lane, built)
        add(f"reviewer.{sid}", f"Reviewer {sid}", (review or {}).get("agent") or "",
            node_state(review) if review else "queued", 4, lane, review)
        if diamond or lane == 0:
            edges.append({"from": "plan-gate", "to": f"builder.{sid}", "kind": "flow", "label": ""})
        else:
            # A single loop's next slice: down from the reviewer, back to the next builder.
            edges.append({"from": f"reviewer.{slices[lane - 1]}", "to": f"builder.{sid}", "kind": "next", "label": ""})
        edges.append({"from": f"builder.{sid}", "to": f"reviewer.{sid}", "kind": "flow", "label": ""})
        if rejects:
            edges.append({"from": f"reviewer.{sid}", "to": f"builder.{sid}", "kind": "back",
                          "label": f"try {built['attempt'] if built else rejects + 1}/{tries}"})
    if diamond and slices:
        merged = "merge" in state.get("approved", {})
        add("merge-gate", "Merge gate", "you approve", "passed" if merged else "you" if state.get("gate") == "merge"
            else "queued", 5, middle)
        for sid in slices:
            edges.append({"from": f"reviewer.{sid}", "to": "merge-gate", "kind": "flow", "label": ""})
        if not stage("integrator"):
            add("integrator", "Integrator", "", "queued", 6, middle)
        edges.append({"from": "merge-gate", "to": "integrator", "kind": "flow", "label": ""})
    return {"nodes": nodes, "edges": edges, "columns": 7 if diamond and slices else 5 if slices else 3,
            "lanes": lanes}


# ---------------------------------------------------------------- the timeline

def timeline(state, activity, now=None):
    """One lane per node: its segments from start to end, each working,
    waiting (for you), or idle; positions as fractions of the run's span.
    Also where the run waited at a gate."""
    now = now or time.time()
    start = state["created"]
    end = state.get("finished") or (state["updated"] if state["status"] in ("done", "cancelled") else now)
    span = max(1.0, end - start)
    lanes, current = {}, {}

    def close(nid, at):
        seg = current.pop(nid, None)
        if seg:
            seg["to"] = at
            lanes.setdefault(nid, []).append(seg)

    def begin(nid, what, at):
        if (current.get(nid) or {}).get("kind") == what:
            return  # still the same: one segment, not two
        close(nid, at)
        current[nid] = {"kind": what, "from": at}

    for entry in activity:
        nid, at, kind = entry.get("node"), entry.get("at") or start, entry.get("event")
        if not nid:
            continue
        if kind in ("started", "working"):
            begin(nid, "working", at)
        elif kind == "needs-input":
            begin(nid, "waiting", at)
        elif kind == "idle":
            begin(nid, "idle", at)
        elif kind in ("ended", "result", "failed", "bad-reply"):
            close(nid, at)
    for nid in list(current):
        close(nid, end)
    gates, open_gate = [], None
    for entry in activity:
        if entry.get("event") == "gate":
            open_gate = {"gate": entry.get("gate"), "from": entry["at"]}
        elif entry.get("event") in ("approved", "sent-back", "cancelled") and open_gate:
            open_gate["to"] = entry["at"]
            gates.append(open_gate)
            open_gate = None
    if open_gate:
        open_gate["to"] = end
        gates.append(open_gate)

    def fraction(at):
        return max(0.0, min(1.0, (at - start) / span))

    ids = list(state["nodes"]) + [nid for nid in lanes if nid not in state["nodes"]]
    if state.get("outside"):
        ids = [nid for nid in ids if lanes.get(nid)]  # its log's lanes: an agent each, not a node
    out = []
    for nid in ids:
        segments = [{"kind": s["kind"], "x": fraction(s["from"]), "w": max(0.004, fraction(s["to"]) - fraction(s["from"])),
                     "minutes": round((s["to"] - s["from"]) / 60, 1)} for s in lanes.get(nid, []) if s["kind"] != "idle"]
        out.append({"node": nid, "segments": segments,
                    "waited": round(sum(s["minutes"] for s in segments if s["kind"] == "waiting"), 1)})
    return {"lanes": out, "gates": [{"gate": g["gate"], "x": fraction(g["from"]),
                                     "w": max(0.004, fraction(g["to"]) - fraction(g["from"]))} for g in gates],
            "length": present.duration(span)}


# ---------------------------------------------------------------- one node

def _result_lines(role, r):
    """A node's result as readable lines (label, text)."""
    if not r:
        return []
    kind = role if role in ("scout", "architect", "builder", "reviewer", "integrator") else "other"
    if kind == "scout":
        return ([("build", r["build"])] + ([("plan-killer", r["plan_killer"])] if r.get("plan_killer") else [])
                + [("fact", f"{f['fact']} ({f['where']})") for f in r["facts"]]
                + [("unknown", u) for u in r["unknowns"]] + [("risk", x) for x in r["risks"]])
    if kind == "architect":
        return ([("shape", r["shape"]), ("why", r["rationale"])]
                + [(s["id"], f"{s['intent']} [{', '.join(s['files'])}]") for s in r["slices"]]
                + [("not doing", "; ".join(r["not_doing"]))])
    if kind == "builder":
        return ([("status", r["status"] + (f": {r['blocked']}" if r.get("blocked") else "")),
                 ("changed", ", ".join(r["changed"])),
                 ("done when", f"{r['done_when']['command']} ({'passed' if r['done_when']['passed'] else 'failed'})"),
                 ("output", r["done_when"]["output"])] + ([("noticed", r["noticed"])] if r.get("noticed") else []))
    if kind == "reviewer":
        return ([("verdict", r["verdict"]), ("summary", r["summary"]), ("re-ran", r["reran"])]
                + [(f["severity"], f"{f['where']}: {f['what']}") for f in r["findings"]])
    if kind == "integrator":
        return ([("merged", ", ".join(r["merged"])), ("suite", f"{r['suite']['command']} "
                                                                f"({'passed' if r['suite']['passed'] else 'failed'})"),
                 ("output", r["suite"]["output"])]
                + [("conflict", f"{', '.join(c['slices'])}: {c['resolution']}") for c in r["conflicts"]]
                + ([("blocked", r["blocked"])] if r.get("blocked") else [])
                + ([("escalate", r["escalate"])] if r.get("escalate") else []))
    return [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in r.items() if v]


def node_detail(state, nid):
    n = fleet.node(state, nid)
    if state.get("outside"):
        brief = (f"({OUTSIDE_LABEL.get(state['outside'], 'an outside run')}: its own files say what it was told; "
                 f"see {state.get('source')})")
    else:
        try:
            brief = fleet.brief(state, nid)
        except fleet.RunError as e:
            brief = f"(no brief: {e})"
    started, ended = n.get("started"), n.get("ended")
    scope = n.get("scope") or {}
    return {
        "id": nid, "role": n["role"], "runsAs": n.get("runs_as") or n["role"], "agent": n.get("agent") or "",
        "slice": n.get("slice") or "", "attempt": n["attempt"], "state": node_state(n), "status": n["status"],
        "session": n.get("session") or "", "error": n.get("error") or "",
        "cost": (n.get("cost") or {}).get("usd") or 0, "costReal": bool((n.get("cost") or {}).get("real")),
        "length": present.duration((ended or time.time()) - started) if started else "",
        "brief": brief, "feedback": n.get("feedback") or [],
        "result": [{"label": k, "text": v} for k, v in _result_lines(n["role"], n.get("result"))],
        "branch": (n.get("git") or {}).get("branch") or "",
        "extra": scope.get("extra") or [], "accepted": (scope.get("accepted") or {}).get("reason") or "",
    }


def team(state):
    """An agent team: its members and its task list."""
    return {"members": [{"name": n["id"], "role": n["role"]} for n in state["nodes"].values()],
            "tasks": state.get("tasks") or []}


def stop_rule_hint(goal):
    """The stop rule, gently: a short goal is probably one task's work."""
    words = len(goal.split())
    if 0 < words < 12:
        return ("A short goal is often one task's work: a single agent from New task is cheaper and as good. "
                "Fleets pay off for several parts, or work you would not merge unreviewed.")
    return ""


def template_shape(spec):
    return {"auto": "scout → architect → gate → builders ⇉ reviewers",
            "single-loop": "scout → architect → gate → builder → reviewer, one slice at a time",
            "diamond": "scout → architect → gate ⇉ builders ⇉ reviewers → merge gate → integrator"}[spec["shape"]]


SHAPES = fleet_graph.SHAPES
