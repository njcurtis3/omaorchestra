"""What each node of a fleet hands back: a JSON block at the end of its reply.

A node's final reply ends with a fenced ```json block in its role's shape
(CONTRACTS). The daemon reads it from the node's transcript, checks it, and
stores it under that node in the run's state: the node never writes the
state itself, so it cannot write another node's part. A missing or bad
block is an error with the reason (the run holds on it), never a guess.

Only the fields a contract names are kept; long command output is cut
short rather than refused; a contradiction (a builder "done" on a failing
command, a REJECT without a blocker) is refused.
"""

import json
import re

FENCED = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S | re.I)
SLICE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")
OUTPUT_LIMIT = 8000


class ReplyError(Exception):
    pass


# ---------------------------------------------------------------- a small schema checker

class Text:
    def __init__(self, limit=2000, cut=False, pattern=None, empty=False):
        self.limit, self.cut, self.pattern, self.empty = limit, cut, pattern, empty

    def check(self, value, where):
        if not isinstance(value, str):
            raise ReplyError(f"{where} must be text")
        value = value.strip()
        if not value and not self.empty:
            raise ReplyError(f"{where} is empty")
        if len(value) > self.limit:
            if not self.cut:
                raise ReplyError(f"{where} is longer than {self.limit} characters")
            value = value[: self.limit - 1] + "…"
        if self.pattern and not self.pattern.match(value):
            raise ReplyError(f"{where} {value!r} is not a valid id (lower-case letters, digits, '-', '_')")
        return value


class OneOf:
    def __init__(self, *values):
        self.values = values

    def check(self, value, where):
        if value not in self.values:
            raise ReplyError(f"{where} must be one of {', '.join(repr(v) for v in self.values)}")
        return value


class Flag:
    def check(self, value, where):
        if not isinstance(value, bool):
            raise ReplyError(f"{where} must be true or false")
        return value


class Many:
    def __init__(self, item, least=0, most=100):
        self.item, self.least, self.most = item, least, most

    def check(self, value, where):
        if not isinstance(value, list):
            raise ReplyError(f"{where} must be a list")
        if len(value) < self.least:
            raise ReplyError(f"{where} needs at least {self.least}")
        if len(value) > self.most:
            raise ReplyError(f"{where} has more than {self.most}")
        return [self.item.check(v, f"{where}[{i}]") for i, v in enumerate(value)]


class Fields:
    """An object: `required` fields must be there; `optional` ones may be
    missing or null (kept as None); anything else is dropped."""

    def __init__(self, required, optional=None):
        self.required, self.optional = required, optional or {}

    def check(self, value, where="the block"):
        if not isinstance(value, dict):
            raise ReplyError(f"{where} must be a JSON object")
        out = {}
        for key, spec in self.required.items():
            if key not in value or value[key] is None:
                raise ReplyError(f"{where} has no {key}" if where != "the block" else f"no {key}")
            out[key] = spec.check(value[key], f"{key}" if where == "the block" else f"{where}.{key}")
        for key, spec in self.optional.items():
            given = value.get(key)
            out[key] = None if given is None or given == "" else \
                spec.check(given, f"{key}" if where == "the block" else f"{where}.{key}")
        return out


def _command(limit=OUTPUT_LIMIT):
    return Fields({"command": Text(1000), "output": Text(limit, cut=True, empty=True), "passed": Flag()})


# ---------------------------------------------------------------- the contracts

# A slice of a plan: the architect's, or one a builder split its own into.
SLICE = Fields({
    "id": Text(20, pattern=SLICE_ID),
    "intent": Text(2000),
    "files": Many(Text(300), least=1, most=200),
    "done_when": Text(1000),
    "risk": OneOf("high", "low"),
    "risk_why": Text(1000),
})
EDGES = Many(Fields({"from": Text(20), "to": Text(20), "artifact": Text(1000)}), most=100)
MAX_CHILDREN = 8

CONTRACTS = {
    "scout": Fields({
        "facts": Many(Fields({"fact": Text(1000), "where": Text(300)}), most=100),
        "unknowns": Many(Text(1000), most=50),
        "risks": Many(Text(1000), most=50),
        "build": OneOf("green", "red", "not run"),
    }, {"plan_killer": Text(1000)}),
    "architect": Fields({
        "shape": OneOf("single-loop", "diamond"),
        "rationale": Text(4000),
        "slices": Many(SLICE, least=1, most=20),
        "not_doing": Many(Text(1000), most=50),
        "approve": Text(2000),
    }, {"edges": EDGES}),
    # `done_when` is required unless the builder split its slice (_consistent).
    "builder": Fields({
        "status": OneOf("done", "blocked", "split"),
        "changed": Many(Text(300), most=500),
    }, {"done_when": _command(), "blocked": Text(2000), "noticed": Text(1000),
        "split": Fields({"rationale": Text(2000), "slices": Many(SLICE, least=2, most=MAX_CHILDREN)},
                        {"edges": EDGES})}),
    "reviewer": Fields({
        "verdict": OneOf("PASS", "REJECT"),
        "findings": Many(Fields({"severity": OneOf("blocker", "note"), "where": Text(300), "what": Text(2000)},
                                {"origin": OneOf("scout", "architect", "builder")}), most=50),
        "summary": Text(1200, cut=True),
        "reran": Text(2000),
    }),
    "integrator": Fields({
        "merged": Many(Text(20), most=20),
        "conflicts": Many(Fields({"slices": Many(Text(20), least=1), "resolution": Text(2000)}), most=50),
        "suite": _command(),
    }, {"blocked": Text(2000), "escalate": Text(2000)}),
    # Any other role: what it did, and whether it got there.
    "other": Fields({"status": OneOf("done", "blocked"), "summary": Text(4000)}, {"blocked": Text(2000)}),
}


def contract_for(role):
    return role if role in CONTRACTS else "other"


def _files(paths, where):
    for path in paths:
        if path.startswith(("/", "~")) or ".." in path.split("/"):
            raise ReplyError(f"{where}: {path} must be a path inside the repository")


def _cycle(slices, edges):
    after = {s: [] for s in slices}
    for edge in edges:
        after[edge["from"]].append(edge["to"])
    state = {}

    def visit(node):
        state[node] = "open"
        for nxt in after[node]:
            if state.get(nxt) == "open" or (nxt not in state and visit(nxt)):
                return True
        state[node] = "done"
        return False
    return any(s not in state and visit(s) for s in slices)


def _plan_consistent(plan, where=""):
    """Slices with distinct ids and paths inside the repository, joined by
    edges that go somewhere and never round in a circle."""
    ids = [s["id"] for s in plan["slices"]]
    if len(set(ids)) != len(ids):
        raise ReplyError(f"{where}two slices share an id")
    for s in plan["slices"]:
        _files(s["files"], f"{where}slice {s['id']}")
    plan["edges"] = plan["edges"] or []
    for edge in plan["edges"]:
        if edge["from"] not in ids or edge["to"] not in ids or edge["from"] == edge["to"]:
            raise ReplyError(f"{where}edge {edge['from']} -> {edge['to']} must join two different slices")
    if _cycle(ids, plan["edges"]):
        raise ReplyError(f"{where}the edges go round in a circle")


def _consistent(role, reply):
    """Refuse what contradicts itself; the shape alone cannot say."""
    if role == "architect":
        _plan_consistent(reply)
    elif role == "builder":
        if reply["status"] == "split":
            if not reply["split"]:
                raise ReplyError("status is split but split does not give the slices")
            _plan_consistent(reply["split"], "split: ")
            return reply
        reply["split"] = None
        if not reply["done_when"]:
            raise ReplyError("no done_when")
        if reply["status"] == "done" and not reply["done_when"]["passed"]:
            raise ReplyError("status is done but its done_when did not pass")
        if reply["status"] == "blocked" and not reply["blocked"]:
            raise ReplyError("status is blocked but blocked does not say why")
    elif role == "reviewer":
        blockers = [f for f in reply["findings"] if f["severity"] == "blocker"]
        if reply["verdict"] == "REJECT" and not blockers:
            raise ReplyError("REJECT needs at least one blocker finding (this input gives this wrong result)")
        if reply["verdict"] == "PASS" and blockers:
            raise ReplyError("PASS with a blocker finding: make it a note, or REJECT")
    elif role == "integrator":
        if reply["suite"]["passed"] is False and not reply["blocked"]:
            raise ReplyError("the full suite failed but blocked does not say which merge broke it")
    elif role == "other":
        if reply["status"] == "blocked" and not reply["blocked"]:
            raise ReplyError("status is blocked but blocked does not say why")
    return reply


def extract(text):
    """The JSON object in the last ```json block of `text`; raises ReplyError."""
    if not text or not text.strip():
        raise ReplyError("its reply could not be read (no final reply found)")
    blocks = FENCED.findall(text)
    if not blocks:
        raise ReplyError("its reply does not end with a ```json block")
    try:
        value = json.loads(blocks[-1])
    except ValueError as e:
        raise ReplyError(f"its ```json block is not valid JSON ({e})") from None
    if not isinstance(value, dict):
        raise ReplyError("its ```json block must be a JSON object")
    return value


def parse(role, text):
    """The checked result for a node of `role` from its final reply; raises
    ReplyError with the reason."""
    kind = contract_for(role)
    return _consistent(kind, CONTRACTS[kind].check(extract(text)))


# ---------------------------------------------------------------- telling the node

EXAMPLES = {
    "scout": {
        "facts": [{"fact": "Login is checked in the session middleware", "where": "src/auth/session.py:42"}],
        "unknowns": ["Whether the mobile app calls the old endpoint"],
        "risks": ["tests/test_login.py already fails on main"],
        "build": "red",
        "plan_killer": "The users table has no email index, so the new lookup scans it",
    },
    "architect": {
        "shape": "single-loop",
        "rationale": "Two slices share src/auth/session.py, so they cannot run in parallel.",
        "slices": [{"id": "s1", "intent": "Look users up by email", "files": ["src/auth/session.py"],
                    "done_when": "python -m pytest tests/test_login.py -> passes", "risk": "high",
                    "risk_why": "It changes how every login is checked"}],
        "edges": [],
        "not_doing": ["Changing the password rules"],
        "approve": "Looking users up by email instead of by name",
    },
    "builder": {
        "status": "done",
        "changed": ["src/auth/session.py", "tests/test_login.py"],
        "done_when": {"command": "python -m pytest tests/test_login.py", "output": "4 passed in 0.31s",
                      "passed": True},
        "blocked": None,
        "noticed": "src/auth/reset.py has the same lookup, left alone",
    },
    "reviewer": {
        "verdict": "REJECT",
        "findings": [{"severity": "blocker", "where": "src/auth/session.py:48",
                      "what": "An email with capitals ('A@x.org') finds no user: the lookup is case-sensitive",
                      "origin": "builder"}],
        "summary": "The lookup works for lower-case emails only. Re-ran the tests (they pass) and tried mixed case.",
        "reran": "python -m pytest tests/test_login.py: 4 passed",
    },
    "integrator": {
        "merged": ["s1", "s2", "s3"],
        "conflicts": [{"slices": ["s1", "s2"], "resolution": "Both added an import; kept both"}],
        "suite": {"command": "python -m pytest", "output": "212 passed in 9.4s", "passed": True},
        "blocked": None,
        "escalate": None,
    },
    "other": {"status": "done", "summary": "What you did, in a few sentences.", "blocked": None},
}

NOTES = {
    "scout": "Every fact has `where`: a file:line (or a URL). `build` is what the tests or build gave when you ran "
             "them, or \"not run\". `plan_killer` is the one finding that changes the plan, or null.",
    "architect": "`shape` is \"single-loop\" or \"diamond\". Slice ids are short (s1, s2...). `files` are paths "
                 "inside the repository; slices must not share files. `done_when` is a command and what it should "
                 "give. `edges` list only slices that use something another produces. `approve` says what exactly "
                 "you need approved.",
    "builder": "`done_when` is the command you ran and its real output; `passed` is whether it passed. `status` is "
               "\"done\" only if it passed; otherwise \"blocked\", with `blocked` saying why. `noticed` is one thing "
               "outside your slice you saw and left alone, or null.",
    "reviewer": "`verdict` is \"PASS\" or \"REJECT\". A REJECT needs at least one \"blocker\" finding (this input "
                "gives this wrong result); anything less is a \"note\". `origin` is who missed it, if one did: "
                "\"scout\", \"architect\" or \"builder\". `summary` is at most 1200 characters.",
    "integrator": "`merged` lists the slice ids you merged. `suite` is the full test suite's command and real "
                  "output. If it failed, `blocked` says which merge broke it; `escalate` is a conflict in meaning "
                  "you refused to resolve, or null.",
    "other": "`status` is \"done\" or \"blocked\", with `blocked` saying why.",
}


SPLIT_EXAMPLE = {
    "status": "split",
    "changed": [],
    "split": {
        "rationale": "The lookup and the reset flow are two changes, each checked on its own.",
        "slices": [
            {"id": "lookup", "intent": "Look users up by email", "files": ["src/auth/session.py"],
             "done_when": "python -m pytest tests/test_login.py -> passes", "risk": "high",
             "risk_why": "It changes how every login is checked"},
            {"id": "reset", "intent": "Send the reset link by email", "files": ["src/auth/reset.py"],
             "done_when": "python -m pytest tests/test_reset.py -> passes", "risk": "low",
             "risk_why": "A flow few people use"}],
        "edges": [{"from": "lookup", "to": "reset", "artifact": "the email lookup"}],
    },
}

SPLIT_NOTE = ("If you split your slice instead of building it, change nothing and end with a block in this shape "
              "instead. omaorchestra starts a new builder and reviewer for each smaller slice, in turn, then a "
              f"reviewer checks your slice as a whole. Give 2 to {MAX_CHILDREN} slices; their `files` must stay "
              "within your slice's files, and `edges` list only those that use something another produces. A split "
              "that changed any file is refused. Never split to get round a review that sent your slice back.")


def reply_format(role, can_split=False):
    """What to end the final reply with, for the node's task; a builder that
    may split its slice is told how."""
    kind = contract_for(role)
    split = ["", "### Or split the slice", "", SPLIT_NOTE, "", "```json",
             json.dumps(SPLIT_EXAMPLE, indent=2, ensure_ascii=False), "```"] if can_split and kind == "builder" else []
    return "\n".join([
        "## Reply format",
        "",
        "End your final reply with one fenced ```json block in exactly this shape (the values here are only an "
        "example). omaorchestra reads it to pass your work on, so it must be valid JSON; nothing else in your "
        "reply is read.",
        "",
        "```json",
        json.dumps(EXAMPLES[kind], indent=2, ensure_ascii=False),
        "```",
        "",
        NOTES[kind],
        *split,
    ])
