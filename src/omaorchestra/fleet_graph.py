"""How a fleet run moves: which nodes start next, where it waits for you.

    scout -> architect -> plan gate -> per slice: builder -> reviewer
      single-loop: one slice at a time, in the plan's order (edges first),
                   in the run's own worktree (or the folder, outside git)
      diamond:     every slice whose edges allow it at once, each in a
                   worktree of its own, then the merge gate and the integrator

A REJECT sends the slice to a new builder (with the findings in its brief)
and then a new reviewer; after `tries` builds of a slice are rejected the
run holds for you. A builder that reports itself blocked holds the run too.

The architect proposes the shape; the fleet can force one. A diamond the run
cannot carry out safely becomes a single loop, with the reason kept in
`shape_note`: outside a git repository (no worktrees), fewer than 3 slices,
two slices touching the same file, or a slice that depends on more than
one other (its worktree would need both).

Fleets (the stages, who plays each, the shape) are built in, and yours go
in ~/.config/omaorchestra/fleets.toml; one with a built-in's name replaces
it:

    [fleets.careful]
    description = "Single loop, reviewed by my security reviewer"
    shape = "single-loop"      # auto (the architect's call), single-loop, diamond
    scout = true               # false: straight to the architect
    tries = 2                  # builds of a slice before a REJECT holds the run
    [fleets.careful.roles]     # the role (roles.py) that plays each stage
    reviewer = "security-reviewer"

The stages and their order are fixed; what a fleet changes is who plays
them, the shape, and the scout.
"""

import time
import tomllib

from . import config, fleet, roles

STAGES = ("scout", "architect", "builder", "reviewer", "integrator")
SHAPES = ("auto", "single-loop", "diamond")
BUILTIN = {
    "auto": {"description": "The architect picks: one builder at a time, or parallel builders", "shape": "auto"},
    "single-loop": {"description": "One builder at a time, each slice reviewed before the next",
                    "shape": "single-loop"},
    "diamond": {"description": "Parallel builders when the plan allows it (checked), then an integrator",
                "shape": "diamond"},
}
KEYS = ("description", "shape", "scout", "tries", "roles")
MIN_DIAMOND = 3


class FleetError(Exception):
    pass


def path():
    return config.path().parent / "fleets.toml"


def _complete(name, spec, builtin):
    out = {"name": name, "description": spec.get("description", ""), "shape": spec.get("shape", "auto"),
           "scout": spec.get("scout", True), "tries": spec.get("tries", 2),
           "roles": {stage: (spec.get("roles") or {}).get(stage, stage) for stage in STAGES}, "builtin": builtin}
    return out


def _check(name, spec, where):
    bad = set(spec) - set(KEYS)
    if bad:
        raise FleetError(f"{where}: fleet {name}: unknown {', '.join(sorted(bad))}")
    if spec.get("shape", "auto") not in SHAPES:
        raise FleetError(f"{where}: fleet {name}: shape must be one of {', '.join(SHAPES)}")
    if not isinstance(spec.get("scout", True), bool):
        raise FleetError(f"{where}: fleet {name}: scout must be true or false")
    tries = spec.get("tries", 2)
    if isinstance(tries, bool) or not isinstance(tries, int) or not 1 <= tries <= 5:
        raise FleetError(f"{where}: fleet {name}: tries must be a whole number from 1 to 5")
    if not isinstance(spec.get("description", ""), str):
        raise FleetError(f"{where}: fleet {name}: description must be text")
    parts = spec.get("roles") or {}
    if not isinstance(parts, dict):
        raise FleetError(f"{where}: fleet {name}: roles must be a table of stage = \"role\"")
    for stage, role in parts.items():
        if stage not in STAGES:
            raise FleetError(f"{where}: fleet {name}: no stage {stage} (stages: {', '.join(STAGES)})")
        if not isinstance(role, str) or not roles.NAME.match(role):
            raise FleetError(f"{where}: fleet {name}: roles.{stage} must be a role's name")


def load(target=None):
    """Built-in and your own fleets, by name; raises FleetError for a bad file."""
    fleets = {name: _complete(name, spec, True) for name, spec in BUILTIN.items()}
    target = target or path()
    try:
        data = tomllib.loads(target.read_text())
    except FileNotFoundError:
        return fleets
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise FleetError(f"{target}: {e}") from None
    own = data.get("fleets") or {}
    if not isinstance(own, dict) or set(data) - {"fleets"}:
        raise FleetError(f"{target}: expected only [fleets.<name>] tables")
    for name, spec in own.items():
        if not isinstance(spec, dict):
            raise FleetError(f"{target}: fleet {name} must be a table")
        _check(name, spec, target)
        fleets[name] = _complete(name, spec, False)
    return fleets


def get(name, target=None):
    fleets = load(target)
    if name not in fleets:
        raise FleetError(f"no fleet {name} (have: {', '.join(sorted(fleets))})")
    return fleets[name]


# ---------------------------------------------------------------- the plan's shape

def depends_on(plan, slice_id):
    return [e["from"] for e in plan.get("edges") or [] if e["to"] == slice_id]


def order(plan):
    """Slice ids with every slice after the ones it depends on, otherwise in
    the plan's order."""
    ids = [s["id"] for s in plan["slices"]]
    done, out = set(), []
    while len(out) < len(ids):
        for sid in ids:
            if sid not in done and all(d in done for d in depends_on(plan, sid)):
                done.add(sid)
                out.append(sid)
                break
        else:
            raise fleet.RunError("the plan's edges go round in a circle")
    return out


def diamond_problem(plan, in_repo):
    """Why this plan cannot run as parallel builders, or None."""
    if not in_repo:
        return "the folder is not in a git repository, so builders cannot have worktrees of their own"
    slices = plan["slices"]
    if len(slices) < MIN_DIAMOND:
        return f"{len(slices)} slice{'s' if len(slices) != 1 else ''}; parallel builders need {MIN_DIAMOND} or more"
    claimed = []  # (path, slice): a folder claims everything under it
    for s in slices:
        for f in s["files"]:
            key = f.strip().rstrip("/").removeprefix("./")
            for other, owner in claimed:
                if owner != s["id"] and (key == other or key.startswith(other + "/") or other.startswith(key + "/")):
                    return f"{owner} and {s['id']} both touch {f if len(key) >= len(other) else other}"
            claimed.append((key, s["id"]))
    for s in slices:
        if len(depends_on(plan, s["id"])) > 1:
            return f"{s['id']} depends on more than one slice"
    return None


def decide_shape(state):
    """The shape the run follows once the plan is in: yours (chosen at the
    gate), else the fleet's, else the architect's; made a single loop when a
    diamond cannot be carried out."""
    plan = fleet.live_plan(state)
    wanted = state.get("shape_choice") or state["template"]["shape"]
    shape = plan["shape"] if wanted == "auto" else wanted
    note = None
    if shape == "diamond":
        problem = diamond_problem(plan, state.get("repo"))
        if problem:
            shape, note = "single-loop", f"single-loop instead of diamond: {problem}"
    state.update(shape=shape, shape_note=note)
    return shape


# ---------------------------------------------------------------- moving on

def passed(state, slice_id):
    """Whether the slice's latest build was reviewed PASS."""
    built = fleet.latest(state, "builder", slice_id)
    review = fleet.latest(state, "reviewer", slice_id)
    return bool(built and review and built["status"] == "done" and review["status"] == "done"
                and review["attempt"] >= built["attempt"] and review["result"]["verdict"] == "PASS")


def _slice_step(state, slice_id):
    """("start", node id), ("wait",), ("passed",) or ("hold", reason)."""
    built = fleet.latest(state, "builder", slice_id)
    if built is None:
        return "start", fleet.add_node(state, "builder", slice_id)["id"]
    if built["status"] != "done":
        return ("wait",)  # running, waiting to start, or already holding the run
    if built["result"]["status"] == "blocked":
        return "hold", f"{built['id']} is blocked: {built['result']['blocked']}"
    review = fleet.latest(state, "reviewer", slice_id)
    if review is None or review["attempt"] < built["attempt"]:
        return "start", fleet.add_node(state, "reviewer", slice_id)["id"]
    if review["status"] != "done":
        return ("wait",)
    if review["result"]["verdict"] == "PASS":
        return ("passed",)
    tries = state["template"]["tries"]
    if built["attempt"] >= tries:
        worst = next((f for f in review["result"]["findings"] if f["severity"] == "blocker"), None)
        return "hold", (f"the reviewer rejected {slice_id} {tries} time{'s' if tries != 1 else ''}"
                        + (f": {worst['where']}: {worst['what']}" if worst else ""))
    return "start", fleet.add_node(state, "builder", slice_id)["id"]


def at_gate(state, gate, now=None):
    state.update(status="at-gate", gate=gate)
    fleet.activity(state["id"], {"event": "gate", "gate": gate}, now)


def finish(state, now=None):
    state.update(status="done", gate=None, finished=now or time.time())
    fleet.activity(state["id"], {"event": "finished"}, now)


def advance(state, now=None):
    """Move the run on as far as it can go now; returns the ids of the nodes
    to start (added, waiting to start). Holds and gates stop it."""
    if state["status"] != "running":
        return []
    template = state["template"]
    if template["scout"]:
        scout = fleet.latest(state, "scout")
        if scout is None:
            return [fleet.add_node(state, "scout")["id"]]
        if scout["status"] != "done":
            return []
    architect = fleet.latest(state, "architect")
    if architect is None:
        return [fleet.add_node(state, "architect")["id"]]
    if architect["status"] != "done":
        return []
    if "plan" not in state["approved"]:
        decide_shape(state)
        at_gate(state, "plan", now)
        return []
    plan = fleet.live_plan(state)
    started = []
    for slice_id in order(plan):
        if not all(passed(state, d) for d in depends_on(plan, slice_id)):
            continue
        step = _slice_step(state, slice_id)
        if step[0] == "passed":
            continue
        if step[0] == "hold":
            fleet.hold(state, step[1], by=slice_id, now=now)
            return []
        if step[0] == "start":
            started.append(step[1])
        if state["shape"] == "single-loop":
            return started  # one slice at a time
    if not all(passed(state, s["id"]) for s in plan["slices"]):
        return started
    if state["shape"] == "single-loop":
        finish(state, now)
        return []
    if "merge" not in state["approved"]:
        at_gate(state, "merge", now)
        return []
    integrator = fleet.latest(state, "integrator")
    if integrator is None:
        return [fleet.add_node(state, "integrator")["id"]]
    if integrator["status"] != "done":
        return []
    result = integrator["result"]
    if result["blocked"] or result["escalate"] or not result["suite"]["passed"]:
        fleet.hold(state, "integrator: " + (result["escalate"] or result["blocked"] or "the full suite failed"),
                   by="integrator", now=now)
        return []
    finish(state, now)
    return []


def _at(state, gate):
    if state["status"] != "at-gate" or state.get("gate") != gate:
        raise fleet.RunError(f"run {state['id']} is not waiting at the {gate} gate")


def send_back(state, note, now=None):
    """The plan goes back to a new architect, with your note (and the plan
    before) in its brief; returns the new architect's node id."""
    _at(state, "plan")
    note = " ".join((note or "").split())
    if not note:
        raise fleet.RunError("say what to change: the note goes to the architect")
    state.update(status="running", gate=None, dropped=[], shape_choice=None)
    nid = fleet.add_node(state, "architect", feedback=[note])["id"]
    fleet.activity(state["id"], {"event": "sent-back", "node": nid, "note": note}, now)
    return nid


def drop(state, slice_id, now=None):
    """Leave a slice out of the plan before approving it."""
    _at(state, "plan")
    plan = fleet.live_plan(state)
    ids = [s["id"] for s in plan["slices"]]
    if slice_id not in ids:
        raise fleet.RunError(f"the plan has no slice {slice_id} to drop")
    if len(ids) == 1:
        raise fleet.RunError("that is the only slice left; cancel the run instead")
    needing = [e["to"] for e in plan["edges"] if e["from"] == slice_id]
    if needing:
        raise fleet.RunError(f"{', '.join(needing)} depend{'s' if len(needing) == 1 else ''} on {slice_id}; "
                             "drop those first")
    state.setdefault("dropped", []).append(slice_id)
    decide_shape(state)
    fleet.activity(state["id"], {"event": "dropped", "slice": slice_id}, now)


def choose_shape(state, shape, now=None):
    """Run the plan as a single loop, or as a diamond (still checked)."""
    _at(state, "plan")
    if shape not in ("single-loop", "diamond"):
        raise fleet.RunError("the shape is single-loop or diamond")
    state["shape_choice"] = shape
    decide_shape(state)
    fleet.activity(state["id"], {"event": "shape", "shape": state["shape"], "note": state.get("shape_note")}, now)


def approve(state, gate, note=None, now=None):
    """You approved the gate the run waits at."""
    _at(state, gate)
    state["approved"][gate] = {"at": now or time.time(), "note": note or None}
    state.update(status="running", gate=None)
    fleet.activity(state["id"], {"event": "approved", "gate": gate}, now)


def cancel(state, now=None):
    """Stop the run: nothing more starts. Returns the nodes that were still
    waiting to start (their queued tasks go too); running sessions are left
    for you to stop."""
    if state["status"] in ("done", "cancelled"):
        raise fleet.RunError(f"run {state['id']} is already {state['status']}")
    waiting = [n for n in state["nodes"].values() if n["status"] == "waiting"]
    for n in waiting:
        n["status"] = "cancelled"
    state.update(status="cancelled", gate=None)
    fleet.activity(state["id"], {"event": "cancelled"}, now)
    return waiting
