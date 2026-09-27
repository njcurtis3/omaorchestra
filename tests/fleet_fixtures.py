"""Fleet runs at every stage, written with the real engine (no daemon, no
agents): for the omafleet tab's tests and screenshots. Call with
OMAORCHESTRA_STATE_DIR (and OMAORCHESTRA_CONFIG) pointing somewhere
throwaway; `folder` should be a git repository."""

import json
import time

from omaorchestra import fleet, fleet_graph, fleet_reply


def _block(value):
    return f"Done.\n\n```json\n{json.dumps(value)}\n```\n"


def _example(role, **changes):
    return {**json.loads(json.dumps(fleet_reply.EXAMPLES[role])), **changes}


def _plan(slices, shape):
    return _example("architect", shape=shape, rationale="Three parts that touch different files.",
                    slices=[{"id": sid, "intent": intent, "files": files, "done_when": "python -m pytest -q -> passes",
                             "risk": risk, "risk_why": why} for sid, intent, files, risk, why in slices],
                    edges=[], not_doing=["Changing the public API", "Styling"],
                    approve="Three slices, built in parallel, then merged")


SLICES = [("s1", "Page the search results", ["search/paging.py"], "low", "new code only"),
          ("s2", "Add the page links to the template", ["templates/search.html"], "low", "a template"),
          ("s3", "Index the search column", ["db/schema.sql"], "high", "a migration on a big table")]


class Clock:
    """Time moves on a few minutes per step, so timelines have a shape."""

    def __init__(self, start):
        self.t = start

    def __call__(self, minutes=3):
        self.t += minutes * 60
        return self.t


def _run(goal, folder, clock, fleet_name="auto", **template):
    state = fleet.create(goal, folder, {**fleet_graph.get(fleet_name), **template}, now=clock(0))
    state["repo"] = True
    return state


def _do(state, clock, nid, reply, minutes=4, **extra):
    fleet.node_started(state, nid, f"S-{state['id'][-6:]}-{nid}", "claude", now=clock(1))
    fleet.activity(state["id"], {"event": "working", "node": nid}, clock(0))
    fleet.record_reply(state, nid, _block(reply), now=clock(minutes), **extra)
    fleet_graph.advance(state, now=clock(0))


def _to_plan(state, clock, shape="diamond"):
    fleet_graph.advance(state, now=clock(0))
    _do(state, clock, "scout", _example("scout"), cost={"usd": 0.12, "real": False})
    _do(state, clock, "architect", _plan(SLICES, shape), cost={"usd": 0.85, "real": False})


def write_all(folder, now=None):
    """Runs at each stage; returns their ids by name."""
    clock = Clock((now or time.time()) - 6 * 3600)
    ids = {}
    PASS = _example("reviewer", verdict="PASS", findings=[], summary="Re-ran the tests; it does what the slice says.")

    # At the plan gate.
    state = _run("Add paging to the search results", folder, clock, budget=5)
    _to_plan(state, clock)
    fleet.save(state)
    ids["plan"] = state["id"]

    # Running a single loop: s1 passed, s2's builder waits for you.
    state = _run("Let people export their notes as Markdown", folder, clock, "single-loop")
    _to_plan(state, clock, "single-loop")
    fleet_graph.approve(state, "plan", now=clock(2))
    state.update(branch="omaorchestra/fleet-" + state["id"])
    fleet_graph.advance(state, now=clock(0))
    _do(state, clock, "builder.s1", _example("builder", changed=["search/paging.py"]), 9,
        cost={"usd": 1.40, "real": False})
    _do(state, clock, "reviewer.s1", PASS, 3, cost={"usd": 0.60, "real": False})
    fleet.node_started(state, "builder.s2", "S-waiting", "claude", now=clock(1))
    fleet.activity(state["id"], {"event": "working", "node": "builder.s2"}, clock(0))
    fleet.activity(state["id"], {"event": "needs-input", "node": "builder.s2"}, clock(5))
    state["nodes"]["builder.s2"]["waiting"] = True
    fleet.save(state, now=clock(0))
    ids["running"] = state["id"]

    # Held: a builder changed files outside its slice.
    state = _run("Retry flaky uploads", folder, clock, "single-loop")
    _to_plan(state, clock, "single-loop")
    fleet_graph.approve(state, "plan", now=clock(1))
    fleet_graph.advance(state, now=clock(0))
    _do(state, clock, "builder.s1", _example("builder"), 12, changed=["CHANGELOG.md", "search/paging.py", "setup.cfg"])
    fleet.save(state)
    ids["scope"] = state["id"]

    # Held: a slice rejected twice.
    state = _run("Cache the avatars", folder, clock, "single-loop")
    _to_plan(state, clock, "single-loop")
    fleet_graph.approve(state, "plan", now=clock(1))
    fleet_graph.advance(state, now=clock(0))
    for attempt in ("", ".2"):
        _do(state, clock, "builder.s1" + attempt, _example("builder"), 8)
        _do(state, clock, "reviewer.s1" + attempt, _example("reviewer"), 4)
    fleet.save(state)
    ids["rejected"] = state["id"]

    # Held: a reply without its JSON block.
    state = _run("Tidy the settings page", folder, clock)
    fleet_graph.advance(state, now=clock(0))
    fleet.node_started(state, "scout", "S-bad", "claude", now=clock(1))
    fleet.record_reply(state, "scout", "I had a look around and everything seems fine.", now=clock(6))
    fleet.save(state)
    ids["bad-reply"] = state["id"]

    # A diamond at its merge gate.
    state = _run("Move the jobs to the new queue", folder, clock)
    _to_plan(state, clock)
    fleet_graph.approve(state, "plan", now=clock(1))
    state.update(branch="omaorchestra/fleet-" + state["id"],
                 slice_worktrees={s[0]: {"branch": f"omaorchestra/fleet-{state['id']}-{s[0]}", "path": "", "workdir": "",
                                         "base": ""} for s in SLICES})
    fleet_graph.advance(state, now=clock(0))
    for sid, *_ in SLICES:
        _do(state, clock, f"builder.{sid}", _example("builder"), 10, cost={"usd": 1.1, "real": False})
    for sid, *_ in SLICES:
        if sid == "s2":
            _do(state, clock, "reviewer.s2", _example("reviewer"), 4)
            _do(state, clock, "builder.s2.2", _example("builder"), 6)
            _do(state, clock, "reviewer.s2.2", PASS, 3)
        else:
            _do(state, clock, f"reviewer.{sid}", PASS, 4)
    fleet.save(state)
    ids["merge"] = state["id"]

    # Finished, not closed.
    state = _run("Fix the date picker on Safari", folder, clock, "single-loop")
    _to_plan(state, clock, "single-loop")
    fleet_graph.approve(state, "plan", now=clock(1))
    state.update(branch="omaorchestra/fleet-" + state["id"])
    for sid, *_ in SLICES:
        fleet_graph.advance(state, now=clock(0))
        _do(state, clock, f"builder.{sid}", _example("builder"), 7, cost={"usd": 0.9, "real": False})
        _do(state, clock, f"reviewer.{sid}", PASS, 3)
    fleet.save(state)
    ids["done"] = state["id"]
    return ids
