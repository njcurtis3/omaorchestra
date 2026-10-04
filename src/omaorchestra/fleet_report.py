"""A fleet run's postmortem: what its shape cost and caught. Read from the
run's state (each node's cost) and its activity (when each worked, waited
for you, held, or sat at a gate); no agent is asked, nothing is written.

  report(state, activity)  one run: time and cost per role and per node,
                           REJECT round-trips per slice, time at gates and
                           holds, and how parallel the builders really ran
  stats(runs, since)       across runs, per role: how many, what they cost,
                           how long they worked, how often builds were sent
                           back (the Usage page, `fleet stats`)

Times are what the activity recorded: a node works from its start (or a
report of working) until it waits for you, goes idle, or ends. Costs are
what its session cost (API-equivalent for a subscription session).

The idea is graph_agents' postmortem (github.com/njcurtis3/graph_agents),
which found builders, not the scout, cost the most: measured here, not
asserted.
"""

import time

from . import fleet

WAITING, WORKING, IDLE = "waiting", "working", "idle"


def segments(activity, end):
    """{node: [(kind, from, to)]}: each node's stretches of working, waiting
    for you, and idle, from the activity, the last one closed at `end`."""
    out, current = {}, {}

    def close(nid, at):
        seg = current.pop(nid, None)
        if seg:
            out.setdefault(nid, []).append((seg[0], seg[1], max(seg[1], at)))

    def begin(nid, kind, at):
        if nid in current and current[nid][0] == kind:
            return  # still the same: one stretch, not two
        close(nid, at)
        current[nid] = (kind, at)

    for entry in activity:
        nid, at, event = entry.get("node"), entry.get("at"), entry.get("event")
        if not nid or not isinstance(at, (int, float)):
            continue
        if event in ("started", "working"):
            begin(nid, WORKING, at)
        elif event == "needs-input":
            begin(nid, WAITING, at)
        elif event == "idle":
            begin(nid, IDLE, at)
        elif event in ("ended", "result", "failed", "bad-reply"):
            close(nid, at)
    for nid in list(current):
        close(nid, end)
    return out


def run_end(state, now=None):
    if state["status"] in ("done", "cancelled"):
        return state.get("finished") or state["updated"]
    return now or time.time()


def _pairs(activity, opens, closes, end, key=None):
    """(label, seconds) for each stretch from an `opens` event to the next
    `closes` one (or `end`)."""
    out, open_at, label = [], None, None
    for entry in activity:
        event = entry.get("event")
        if event in opens and open_at is None:
            open_at, label = entry.get("at"), entry.get(key) if key else None
        elif event in closes and open_at is not None:
            out.append((label, entry.get("at", open_at) - open_at))
            open_at = None
    if open_at is not None:
        out.append((label, end - open_at))
    return out


def _overlap(intervals):
    """(most at once, share of the busy time when two or more ran) for a
    list of (from, to)."""
    points = sorted([(a, 1) for a, b in intervals if b > a] + [(b, -1) for a, b in intervals if b > a])
    most, running, busy, together, last = 0, 0, 0.0, 0.0, None
    for at, step in points:
        if last is not None and running:
            busy += at - last
            if running >= 2:
                together += at - last
        running += step
        most = max(most, running)
        last = at
    return most, (together / busy if busy else 0.0)


def report(state, activity, now=None):
    end = run_end(state, now)
    if state.get("outside") and activity:
        end = max(e["at"] for e in activity)  # its file may be touched long after the run
    span = max(0.0, end - state["created"])
    stretches = segments(activity, end)
    nodes = []
    for n in state["nodes"].values():
        segs = stretches.get(n["id"], [])
        working = sum(b - a for kind, a, b in segs if kind == WORKING)
        waiting = sum(b - a for kind, a, b in segs if kind == WAITING)
        nodes.append({"id": n["id"], "role": n["role"], "slice": n.get("slice"), "attempt": n["attempt"],
                      "status": n["status"], "cost": round((n.get("cost") or {}).get("usd") or 0, 4),
                      "real": bool((n.get("cost") or {}).get("real")), "working_s": round(working),
                      "waiting_s": round(waiting), "waits": sum(1 for kind, *_ in segs if kind == WAITING)})
    roles = {}
    for n in [] if state.get("outside") else nodes:  # an outside run's roles come from its lanes
        r = roles.setdefault(n["role"], {"role": n["role"], "nodes": 0, "cost": 0.0, "working_s": 0, "waiting_s": 0})
        r["nodes"] += 1
        r["cost"] = round(r["cost"] + n["cost"], 4)
        r["working_s"] += n["working_s"]
        r["waiting_s"] += n["waiting_s"]
    # A lane that is not a node (graph_agents logs an agent, "builder a3f",
    # not a slice) counts for the role it names.
    lanes = {nid: segs for nid, segs in stretches.items() if nid not in state["nodes"]}
    for lane, segs in lanes.items():
        role = lane.split()[0]
        r = roles.setdefault(role, {"role": role, "nodes": 0, "cost": 0.0, "working_s": 0, "waiting_s": 0})
        r["nodes"] += 1
        r["working_s"] += round(sum(b - a for kind, a, b in segs if kind == WORKING))
    total_cost = round(sum(n["cost"] for n in nodes), 4)
    for r in roles.values():
        r["share"] = round(r["cost"] / total_cost, 3) if total_cost else 0.0
    slices = []
    for row in fleet.board(state):  # a split slice's smaller slices too, after it
        mine = [n for n in state["nodes"].values() if n.get("slice") == row["slice"]]
        reviews = [n for n in mine if n["role"] == "reviewer" and n["status"] == "done"]
        latest = max(reviews, key=lambda n: n["attempt"]) if reviews else None
        slices.append({"slice": row["slice"], "depth": row["depth"], "builds": sum(1 for n in mine if n["role"] == "builder"),
                       "rejects": sum(1 for n in reviews if n["result"]["verdict"] == "REJECT"),
                       "verdict": latest["result"]["verdict"] if latest else None,
                       "extra_files": sum(len((n.get("scope") or {}).get("extra") or []) for n in mine
                                          if n["role"] == "builder"),
                       "cost": round(sum((n.get("cost") or {}).get("usd") or 0 for n in mine), 4)})
    gates = [{"gate": gate, "waited_s": round(s)} for gate, s in
             _pairs(activity, ("gate",), ("approved", "sent-back", "cancelled"), end, key="gate")]
    holds = _pairs(activity, ("held",), ("released", "retry", "cancelled", "finished"), end, key="reason")
    builders = [(a, b) for nid, segs in stretches.items()
                if (state["nodes"].get(nid) or {}).get("role") == "builder" or nid.split()[0] == "builder"
                for kind, a, b in segs if kind == WORKING]
    most, together = _overlap(builders)
    worked = sum(r["working_s"] for r in roles.values())
    return {
        "run": state["id"], "goal": state["goal"], "status": state["status"], "shape": state.get("shape"),
        "span_s": round(span), "cost": total_cost, "estimated": not any(n["real"] for n in nodes if n["cost"]),
        "roles": sorted(roles.values(), key=lambda r: -r["cost"]), "nodes": nodes, "slices": slices,
        "rejects": sum(s["rejects"] for s in slices),
        "sent_back": sum(max(0, s["builds"] - 1) for s in slices),
        "retries": sum(1 for e in activity if e.get("event") == "retry"),
        "stalls": sum(1 for e in activity if e.get("event") == "stalled"),
        "gates": gates, "holds": {"count": len(holds), "held_s": round(sum(s for _, s in holds)),
                                  "reasons": [reason for reason, _ in holds if reason]},
        "parallel": {"builders_at_once": most, "builders_together": round(together, 3),
                     "busy": round(worked / span, 2) if span else 0.0},
    }


def stats(states, since=None, read_activity=fleet.read_activity):
    """Across runs (started since `since`), per role: nodes, cost, average
    cost, working time, and for reviewers how often they rejected."""
    runs = [s for s in states if not s.get("outside") and (since is None or s["created"] >= since)]
    roles = {}
    for state in runs:
        for n in state["nodes"].values():
            r = roles.setdefault(n["role"], {"role": n["role"], "nodes": 0, "cost": 0.0, "working_s": 0,
                                             "done": 0, "rejected": 0})
            r["nodes"] += 1
            r["cost"] = round(r["cost"] + ((n.get("cost") or {}).get("usd") or 0), 4)
            if n["status"] == "done":
                r["done"] += 1
                result = n.get("result") or {}
                if n["role"] == "reviewer" and result.get("verdict") == "REJECT":
                    r["rejected"] += 1
    for state in runs:
        end = run_end(state)
        for nid, segs in segments(read_activity(state["id"]), end).items():
            role = state["nodes"].get(nid, {}).get("role")
            if role in roles:
                roles[role]["working_s"] += round(sum(b - a for kind, a, b in segs if kind == WORKING))
    reviewers = roles.get("reviewer", {})
    total = round(sum(r["cost"] for r in roles.values()), 4)
    for r in roles.values():
        r["average"] = round(r["cost"] / r["nodes"], 4) if r["nodes"] else 0.0
        r["share"] = round(r["cost"] / total, 3) if total else 0.0
    return {"runs": len(runs), "cost": total, "per_run": round(total / len(runs), 4) if runs else 0.0,
            "reject_rate": round(reviewers.get("rejected", 0) / reviewers["done"], 3) if reviewers.get("done") else 0.0,
            "roles": sorted(roles.values(), key=lambda r: -r["cost"])}
