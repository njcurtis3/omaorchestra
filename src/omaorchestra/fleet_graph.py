"""How a fleet run moves: which nodes start next, where it waits for you.

    scout -> architect -> plan gate -> per slice: builder -> reviewer
      single-loop: one slice at a time, in the plan's order (edges first),
                   in the run's own worktree (or the folder, outside git)
      diamond:     every slice whose edges allow it at once, each in a
                   worktree of its own, then the merge gate and the integrator

A REJECT sends the slice to a new builder (with the findings in its brief)
and then a new reviewer; after `tries` builds of a slice are rejected the
run holds for you. A builder that reports itself blocked holds the run too.

A builder may split its slice instead of building it, when the fleet's
`max_depth` allows (1, the default, allows none; 2 lets a slice of the plan
split once). Its smaller slices (s2-a, s2-b...) must stay within the
slice's files; they run one at a time in the slice's own worktree, each
built and reviewed like any slice, and may split again while the depth
allows. When all of them have passed, a reviewer checks the slice as a
whole. A split does not wait for you unless the fleet sets `split_gate`:
it cannot reach past files you already approved, and the scope check
holds any builder that does.

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
    budget = 5                 # US$ the run may spend (API-equivalent); 0: no budget
    max_steps = 30             # nodes the run may start in all
    stall_minutes = 20         # a node working this long without a sign of life is flagged
    max_depth = 1              # 2: a builder may split its slice into smaller ones, once
    split_gate = false         # true: a split waits for you to approve it
    [fleets.careful.roles]     # the role (roles.py) that plays each stage
    reviewer = "security-reviewer"

The stages and their order are fixed; what a fleet changes is who plays
them, the shape, the scout, and how deep a slice may split.
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
# Claude Code's permission mode for a run's agents unless their role sets one: in
# auto mode they go on without asking you to say yes to each command.
PERMISSION_MODE = "auto"
KEYS = ("description", "shape", "scout", "tries", "roles", "budget", "max_steps", "stall_minutes", "max_depth",
        "split_gate", "permission_mode")
MIN_DIAMOND = 3
EXAMPLE = """[fleets.careful]
description = "Single loop, reviewed by my security reviewer"
shape = "single-loop"        # auto (the architect's call), single-loop, diamond
scout = true                 # false: straight to the architect
tries = 2                    # builds of a slice before a REJECT holds the run
budget = 5                   # US$ the run may spend (API-equivalent); 0: no budget
max_steps = 30               # agents the run may start in all
stall_minutes = 20           # working this long with no sign of life is flagged
max_depth = 1                # 2: a builder may split its slice into smaller ones, once
split_gate = false           # true: a split waits for you to approve it
[fleets.careful.roles]       # the role (roles.py) that plays each stage
reviewer = "security-reviewer"
"""


class FleetError(Exception):
    pass


def path():
    return config.path().parent / "fleets.toml"


def _complete(name, spec, builtin):
    out = {"name": name, "description": spec.get("description", ""), "shape": spec.get("shape", "auto"),
           "scout": spec.get("scout", True), "tries": spec.get("tries", 2), "budget": spec.get("budget", 0),
           "max_steps": spec.get("max_steps", 30), "stall_minutes": spec.get("stall_minutes", 20),
           "max_depth": spec.get("max_depth", 1), "split_gate": spec.get("split_gate", False),
           "permission_mode": spec.get("permission_mode", PERMISSION_MODE),
           "roles": {stage: (spec.get("roles") or {}).get(stage, stage) for stage in STAGES}, "builtin": builtin}
    return out


def _check(name, spec, where):
    bad = set(spec) - set(KEYS)
    if bad:
        raise FleetError(f"{where}: fleet {name}: unknown {', '.join(sorted(bad))}")
    if spec.get("shape", "auto") not in SHAPES:
        raise FleetError(f"{where}: fleet {name}: shape must be one of {', '.join(SHAPES)}")
    for key in ("scout", "split_gate"):
        if not isinstance(spec.get(key, False), bool):
            raise FleetError(f"{where}: fleet {name}: {key} must be true or false")
    if spec.get("permission_mode", PERMISSION_MODE) not in roles.PERMISSION_MODES:
        raise FleetError(f"{where}: fleet {name}: permission_mode must be one of {', '.join(roles.PERMISSION_MODES)}")
    tries = spec.get("tries", 2)
    if isinstance(tries, bool) or not isinstance(tries, int) or not 1 <= tries <= 5:
        raise FleetError(f"{where}: fleet {name}: tries must be a whole number from 1 to 5")
    budget = spec.get("budget", 0)
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 <= budget <= 10000:
        raise FleetError(f"{where}: fleet {name}: budget must be US$ from 0 (no budget) to 10000")
    for key, low, high in (("max_steps", 3, 200), ("stall_minutes", 1, 240), ("max_depth", 1, 4)):
        value = spec.get(key, low)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise FleetError(f"{where}: fleet {name}: {key} must be a whole number from {low} to {high}")
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


def expected_branch(state, n):
    """The branch a builder or the integrator must end on: the slice's own
    in a diamond, else the run's. None outside git."""
    if n["role"] == "builder" and state.get("shape") == "diamond":
        return (state.get("slice_worktrees", {}).get(fleet.root_slice(state, n.get("slice"))) or {}).get("branch")
    return state.get("branch")


def off_branch(state, n):
    """Why a node that writes finished somewhere it should not, or None: a
    run's work never lands on your own branches."""
    wanted = expected_branch(state, n)
    if not wanted or not n.get("git"):
        return None
    ended = n["git"].get("branch")
    if ended != wanted:
        return f"{n['id']} ended on {ended or 'a detached HEAD'}, not {wanted}"
    return None


class _Limit(Exception):
    pass


def _add(state, role, slice_id=None):
    """A new node, unless the run has reached its step limit."""
    cap = (state.get("limits") or {}).get("max_steps")
    if cap and len(state["nodes"]) >= cap:
        raise _Limit(f"the run reached its limit of {cap} steps")
    return fleet.add_node(state, role, slice_id)["id"]


def over_budget(state):
    budget = (state.get("limits") or {}).get("budget")
    spent = fleet.spent(state)
    if budget and spent >= budget:
        return f"the run has spent ${spent:.2f}, at its ${budget} budget"
    return None


def _slice_step(state, slice_id):
    """("start", node id), ("wait",), ("passed",), ("hold", reason) or
    ("gate", builder id): a split waiting for you."""
    built = fleet.latest(state, "builder", slice_id)
    if built is None:
        return "start", _add(state, "builder", slice_id)
    if built["status"] != "done":
        return ("wait",)  # running, waiting to start, or already holding the run
    if off_branch(state, built):
        return "hold", off_branch(state, built)
    if built["result"]["status"] == "blocked":
        return "hold", f"{built['id']} is blocked: {built['result']['blocked']}"
    scope = built.get("scope") or {}
    if scope.get("extra") and not scope.get("accepted"):
        return "hold", f"{built['id']} changed files outside its slice: {', '.join(scope['extra'][:8])}" + \
            (f" and {len(scope['extra']) - 8} more" if len(scope["extra"]) > 8 else ""), built["id"]
    split = fleet.split_plan(built)
    if split:
        if state["template"].get("split_gate") and f"split:{built['id']}" not in state["approved"]:
            return "gate", built["id"]
        step = _split_step(state, split)
        if step[0] != "passed":
            return step
        # Every smaller slice passed: now the slice is reviewed as a whole.
    review = fleet.latest(state, "reviewer", slice_id)
    if review is None or review["attempt"] < built["attempt"]:
        return "start", _add(state, "reviewer", slice_id)
    if review["status"] != "done":
        return ("wait",)
    if review["result"]["verdict"] == "PASS":
        return ("passed",)
    tries = state["template"]["tries"]
    if built["attempt"] >= tries:
        worst = next((f for f in review["result"]["findings"] if f["severity"] == "blocker"), None)
        return "hold", (f"the reviewer rejected {slice_id} {tries} time{'s' if tries != 1 else ''}"
                        + (f": {worst['where']}: {worst['what']}" if worst else ""))
    return "start", _add(state, "builder", slice_id)


def _split_step(state, split):
    """The next step of a split's smaller slices, one at a time in the order
    their edges allow; ("passed",) once they all have."""
    for slice_id in order(split):
        step = _slice_step(state, slice_id)
        if step[0] == "hold" and len(step) == 2:
            return "hold", step[1], slice_id  # held by this slice, not the one it came from
        if step[0] != "passed":
            return step
    return ("passed",)


def at_gate(state, gate, now=None):
    state.update(status="at-gate", gate=gate, since=now or time.time())
    fleet.activity(state["id"], {"event": "gate", "gate": gate}, now)


def finish(state, now=None):
    state.update(status="done", gate=None, finished=now or time.time())
    fleet.activity(state["id"], {"event": "finished"}, now)


def advance(state, now=None):
    """Move the run on as far as it can go now; returns the ids of the nodes
    to start (added, waiting to start). Holds and gates stop it."""
    if state["status"] != "running":
        return []
    reason = over_budget(state)
    if reason:
        fleet.hold(state, reason, by="limits", now=now)
        return []
    started = []
    try:
        return _advance(state, started, now)
    except _Limit as e:
        fleet.hold(state, str(e), by="limits", now=now)
        return started


def _advance(state, started, now):
    template = state["template"]
    if template["scout"]:
        scout = fleet.latest(state, "scout")
        if scout is None:
            return [_add(state, "scout")]
        if scout["status"] != "done":
            return []
    architect = fleet.latest(state, "architect")
    if architect is None:
        return [_add(state, "architect")]
    if architect["status"] != "done":
        return []
    if "plan" not in state["approved"]:
        decide_shape(state)
        at_gate(state, "plan", now)
        return []
    plan = fleet.live_plan(state)
    for slice_id in order(plan):
        if not all(passed(state, d) for d in depends_on(plan, slice_id)):
            continue
        step = _slice_step(state, slice_id)
        if step[0] == "passed":
            continue
        if step[0] == "hold":
            fleet.hold(state, step[1], by=step[2] if len(step) > 2 else slice_id, now=now)
            return started  # nodes another slice already started still go
        if step[0] == "gate":
            state["split_node"] = step[1]
            at_gate(state, "split", now)
            return started
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
        return [_add(state, "integrator")]
    if integrator["status"] != "done":
        return []
    result = integrator["result"]
    missing = [s["id"] for s in plan["slices"] if s["id"] not in result["merged"]]
    reason = (off_branch(state, integrator)
              or (result["escalate"] or result["blocked"] or ("the full suite failed" if not result["suite"]["passed"]
                                                                else None))
              or (f"it did not merge {', '.join(missing)}" if missing else None))
    if reason:
        fleet.hold(state, reason if reason.startswith("integrator") else f"integrator: {reason}", by="integrator",
                   now=now)
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
    state.update(status="running", gate=None, since=None, dropped=[], shape_choice=None)
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


def accept_scope(state, nid, reason, now=None):
    """You accept the files a builder changed outside its slice, saying why;
    the slice goes on to review."""
    n = fleet.node(state, nid)
    scope = n.get("scope") or {}
    if n["role"] != "builder" or not scope.get("extra"):
        raise fleet.RunError(f"{nid} changed no files outside its slice")
    if scope.get("accepted"):
        raise fleet.RunError(f"{nid}'s files were already accepted")
    reason = " ".join((reason or "").split())
    if not reason:
        raise fleet.RunError("say why: the reason is recorded with the files")
    scope["accepted"] = {"reason": reason, "at": now or time.time()}
    fleet.activity(state["id"], {"event": "scope-accepted", "node": nid, "files": scope["extra"], "reason": reason},
                   now)
    if state.get("held_by") == nid:
        fleet.release(state, now)


def scope_send_back(state, nid, now=None):
    """The slice goes to a new builder, told which files to undo; returns
    its node id."""
    n = fleet.node(state, nid)
    scope = n.get("scope") or {}
    if n["role"] != "builder" or not scope.get("extra") or scope.get("accepted"):
        raise fleet.RunError(f"{nid} has no files outside its slice to send back")
    if fleet.latest(state, "builder", n["slice"])["id"] != nid:
        raise fleet.RunError(f"{nid} is not the slice's latest build")
    note = ("Your slice's last build changed files outside it: " + ", ".join(scope["extra"])
            + ". Undo those changes (they must not stay in the slice's commits) and keep to the approved files.")
    new = fleet.add_node(state, "builder", n["slice"], feedback=[note])["id"]
    fleet.activity(state["id"], {"event": "scope-sent-back", "node": nid, "next": new}, now)
    if state.get("held_by") == nid:
        fleet.release(state, now)
    return new


def retry(state, note=None, now=None):
    """A held run tries again: a new attempt of what held it (a node that
    failed or replied badly, a slice rejected too often or blocked, the
    integrator), with your note in its brief. Returns the new node's id,
    or None when the run just goes on (it held on something else). The
    budget, steps and a builder's extra files have their own answers."""
    if state["status"] != "held":
        raise fleet.RunError(f"run {state['id']} is not held")
    held = state.get("held_by")
    if held == "limits":
        raise fleet.RunError("it is held by its limits: raise them instead")
    if held == "paused":
        raise fleet.RunError("it is paused: resume it instead")
    n = state["nodes"].get(held) if held else None
    feedback = [" ".join(note.split())] if note and note.strip() else []
    if n and n.get("error_kind") == "reply" and n.get("error"):
        # The new attempt is told why the last one was refused, so it does not repeat it.
        said = n["error"].removeprefix("its reply: ").removeprefix("its ")
        feedback.insert(0, f"omaorchestra refused the last {n['role']}'s reply: {said}. Give a reply that avoids "
                           "this.")
    if n and n["role"] == "builder" and (n.get("scope") or {}).get("extra") and not n["scope"].get("accepted"):
        raise fleet.RunError("it is held by a builder's extra files: accept them or send the slice back")
    if n:
        role, slice_id = n["role"], n.get("slice")
        if n["status"] in ("held", "running", "waiting"):
            n["status"] = "failed"  # replaced by the new attempt
    elif held and fleet.slice_of(state, held):
        role, slice_id = "builder", held
    else:
        role = None
    new = fleet.add_node(state, role, slice_id, feedback=feedback)["id"] if role else None
    fleet.activity(state["id"], {"event": "retry", "held_by": held, "next": new, "note": note or None}, now)
    fleet.release(state, now)
    return new


def pause(state, now=None):
    """Hold a running run: nothing new starts (nodes already working go on)."""
    if state["status"] != "running":
        raise fleet.RunError(f"run {state['id']} is not running")
    fleet.hold(state, "paused by you", by="paused", now=now)


def resume(state, now=None):
    if state["status"] != "held" or state.get("held_by") != "paused":
        raise fleet.RunError(f"run {state['id']} is not paused")
    fleet.release(state, now)


def set_limits(state, budget=None, max_steps=None, now=None):
    """Change a run's budget (US$, 0 for none) or step limit; a run held by
    its limits goes on if they now allow it."""
    limits = state.setdefault("limits", {})
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 <= budget <= 10000:
            raise fleet.RunError("the budget is US$ from 0 (none) to 10000")
        limits["budget"] = budget
    if max_steps is not None:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 3 <= max_steps <= 200:
            raise fleet.RunError("the step limit is a whole number from 3 to 200")
        limits["max_steps"] = max_steps
    fleet.activity(state["id"], {"event": "limits", **limits}, now)
    if state.get("held_by") == "limits":
        fleet.release(state, now)


def approve(state, gate, note=None, now=None):
    """You approved the gate the run waits at (a split's is kept per
    builder, as `split:<builder id>`)."""
    _at(state, gate)
    key = f"split:{state['split_node']}" if gate == "split" else gate
    state["approved"][key] = {"at": now or time.time(), "note": note or None}
    state.update(status="running", gate=None, since=None)
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
