"""Roles: an agent with a part to play (scout, architect, builder, reviewer,
integrator), for fleets and for `run --role`.

A role is a Markdown file in Claude Code's subagent format: frontmatter
(`name`, `description`, `tools`, `disallowedTools`, `model`,
`permissionMode`, `effort`) and the role's prompt as the body. One more
field is omaorchestra's own, and Claude ignores it: `agent`, which agent
runs the role (claude, the default, codex or opencode).

Three places, the first that has a name wins:

  project   .claude/agents/**/*.md in the folder and up to its repository's
            root, closest first (so a project's Claude subagents are roles)
  yours     ~/.config/omaorchestra/roles/*.md
  built-in  scout, architect, builder, reviewer, integrator, adapted from
            the owner's graph_agents fleet (github.com/njcurtis3/graph_agents)

How each agent takes on a role (launch_args):

  claude    the role as a session-only agent (`--agents`) and run as it
            (`--agent`): its prompt, tools and model become the session's
  codex     the prompt as developer instructions (`-c developer_instructions`)
            and, for a read-only role, the read-only sandbox
  opencode  the role as an inline agent (OPENCODE_CONFIG_CONTENT) run with
            `--agent`, its edit, bash and web tools denied when the role
            lacks them

A read-only role is one whose tools include none that write (Write, Edit,
NotebookEdit). Claude's own permission modes and model names apply only when
Claude runs the role; another agent keeps its own defaults.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

BUILTIN_DIR = Path(__file__).parent / "builtin_roles"
FLEET_ROLES = ("scout", "architect", "builder", "reviewer", "integrator")
AGENTS = config.KNOWN_AGENTS
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
CLAUDE_ALIASES = ("opus", "sonnet", "haiku", "fable", "inherit")
PERMISSION_MODES = ("default", "manual", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SOURCES = ("project", "yours", "built-in")


class RoleError(Exception):
    pass


@dataclass
class Role:
    name: str
    prompt: str
    description: str = ""
    tools: tuple = None  # None: every tool the agent has
    disallowed_tools: tuple = ()
    model: str = None
    permission_mode: str = None
    effort: str = None
    agent: str = "claude"
    source: str = "built-in"
    path: str = ""
    shadows: list = field(default_factory=list)  # the sources this one hides, by name

    @property
    def read_only(self):
        names = {tool_name(t) for t in self.tools} if self.tools is not None else None
        if names is not None and not names & set(WRITE_TOOLS):
            return True
        return {tool_name(t) for t in self.disallowed_tools} >= {"Write", "Edit"}

    def allows(self, tool):
        """Whether the role has a tool (by name, ignoring any `(pattern)`)."""
        denied = {tool_name(t) for t in self.disallowed_tools}
        return tool not in denied and (self.tools is None or tool in {tool_name(t) for t in self.tools})

    def model_for(self, agent):
        """The model to run the role with on `agent`, or None for its default:
        Claude's model names mean nothing to another agent."""
        if not self.model or self.model == "inherit":
            return None
        if agent != "claude" and is_claude_model(self.model):
            return None
        return self.model

    def summary(self):
        return {"name": self.name, "description": self.description, "agent": self.agent, "model": self.model,
                "permission_mode": self.permission_mode, "effort": self.effort,
                "tools": list(self.tools) if self.tools is not None else None,
                "disallowed_tools": list(self.disallowed_tools), "read_only": self.read_only,
                "source": self.source, "path": self.path, "shadows": self.shadows}


def tool_name(tool):
    return tool.split("(", 1)[0].strip()


def is_claude_model(model):
    return model in CLAUDE_ALIASES or model.startswith("claude-")


# ---------------------------------------------------------------- frontmatter

def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError:
            return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip()
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(part) for part in text[1:-1].split(",") if part.strip()]
    return text


def frontmatter(text):
    """(fields, body) from a Markdown file with YAML frontmatter. Only the
    subset subagent files use: `key: value`, `key: a, b`, `[a, b]`, `- item`
    lists and `|`/`>` blocks; a nested table (hooks, mcpServers) is kept as
    None, since roles do not use it. Raises RoleError."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise RoleError("no frontmatter (the file must start with a --- line)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise RoleError("the frontmatter has no closing --- line") from None
    fields, i, head = {}, 0, lines[1:end]
    while i < len(head):
        line = head[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            raise RoleError(f"unexpected indented line: {line.strip()}")
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise RoleError(f"not a `key: value` line: {line.strip()}")
        key, value = key.strip(), value.strip()
        block = []
        while i < len(head) and (not head[i].strip() or head[i][0] in " \t"):
            block.append(head[i])
            i += 1
        while block and not block[-1].strip():
            block.pop()
        if value in ("|", "|-", ">", ">-"):
            parts = [b.strip() for b in block]
            fields[key] = ("\n" if value.startswith("|") else " ").join(parts).strip()
        elif value:
            fields[key] = _scalar(value)
        elif block and all(b.strip().startswith("- ") or not b.strip() for b in block):
            fields[key] = [_scalar(b.strip()[2:]) for b in block if b.strip()]
        else:
            fields[key] = None  # empty, or a nested table
    return fields, "\n".join(lines[end + 1:]).strip() + "\n"


def _tools(value, key):
    if value is None:
        return None
    if isinstance(value, str):
        value = [part for part in re.split(r",\s*|\s+(?![^(]*\))", value) if part]
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise RoleError(f"{key} must be a list of tool names")
    return tuple(t.strip() for t in value if t.strip())


def parse(text, source="yours", path="", fallback_name=None):
    """A Role from a role file's text; raises RoleError."""
    fields, body = frontmatter(text)
    name = fields.get("name") or fallback_name
    if not isinstance(name, str) or not NAME.match(name):
        raise RoleError(f"name {name!r} must be letters, digits, '-', '_' or '.', starting with a letter or digit")
    if not body.strip():
        raise RoleError("the role has no prompt (the text after the frontmatter)")
    agent = fields.get("agent") or "claude"
    if agent not in AGENTS:
        raise RoleError(f"agent must be one of {', '.join(AGENTS)}, not {agent}")
    model = fields.get("model")
    if model is not None and (not isinstance(model, str) or any(c.isspace() for c in model)):
        raise RoleError("model must be a model name")
    mode = fields.get("permissionMode")
    if mode is not None and mode not in PERMISSION_MODES:
        raise RoleError(f"permissionMode must be one of {', '.join(PERMISSION_MODES)}")
    effort = fields.get("effort")
    if effort is not None and effort not in EFFORTS:
        raise RoleError(f"effort must be one of {', '.join(EFFORTS)}")
    description = fields.get("description") or ""
    if not isinstance(description, str):
        raise RoleError("description must be text")
    return Role(name=name, prompt=body, description=description, tools=_tools(fields.get("tools"), "tools"),
                disallowed_tools=_tools(fields.get("disallowedTools"), "disallowedTools") or (),
                model=model, permission_mode=mode, effort=effort, agent=agent, source=source, path=str(path))


# ---------------------------------------------------------------- where roles live

def user_dir():
    return config.path().parent / "roles"


def project_dirs(folder):
    """`.claude/agents` folders from `folder` up to its repository's root,
    closest first; only `folder`'s own when it is not in a repository."""
    if not folder:
        return []
    from . import worktrees
    folder = Path(folder).expanduser().resolve()
    root = worktrees.repo_root(folder)
    root = Path(root).resolve() if root else folder
    dirs, here = [], folder
    while True:
        candidate = here / ".claude" / "agents"
        if candidate.is_dir():
            dirs.append(candidate)
        if here == root or here.parent == here:
            break
        here = here.parent
    return dirs


def _read(path, source, recursive=False):
    files = sorted(path.rglob("*.md") if recursive else path.glob("*.md"))
    roles, problems = [], []
    for file in files:
        try:
            roles.append(parse(file.read_text(), source, file, fallback_name=file.stem))
        except (OSError, UnicodeDecodeError, RoleError) as e:
            problems.append(f"{file}: {e}")
    return roles, problems


def load(folder=None, user=None, builtin=None):
    """(roles by name, problems). Every role found, the winner per name
    first; a file that cannot be read is a problem, never fatal."""
    found, problems = [], []
    for directory in project_dirs(folder):
        roles, bad = _read(directory, "project", recursive=True)
        found += roles
        problems += bad
    user = Path(user) if user else user_dir()
    if user.is_dir():
        roles, bad = _read(user, "yours")
        found += roles
        problems += bad
    roles, bad = _read(Path(builtin) if builtin else BUILTIN_DIR, "built-in")
    found += roles
    problems += bad
    winners = {}
    for role in found:
        if role.name in winners:
            if role.source not in winners[role.name].shadows and role.source != winners[role.name].source:
                winners[role.name].shadows.append(role.source)
            continue
        winners[role.name] = role
    return winners, problems


def get(name, folder=None, user=None, builtin=None):
    roles, _ = load(folder, user, builtin)
    if name not in roles:
        raise RoleError(f"no role {name} (have: {', '.join(sorted(roles))})")
    return roles[name]


# ---------------------------------------------------------------- running a role

def _toml_string(text):
    # A TOML basic string: JSON's escapes are valid TOML, as long as non-ASCII
    # stays as is (TOML rejects the surrogate pairs JSON would write).
    return json.dumps(text, ensure_ascii=False)


def opencode_name(role):
    # Prefixed, so a role never replaces one of the user's own opencode agents.
    return f"omaorchestra-{role.name}"


def launch_args(role, agent, environ=None):
    """How `agent` runs as `role`: {"extra": args for the agent's command
    line, "env": variables to add, "model", "permission_mode"}."""
    environ = environ or {}
    spec = {"extra": [], "env": {}, "model": role.model_for(agent), "permission_mode": None}
    if agent == "claude":
        definition = {"description": role.description or f"The {role.name} role", "prompt": role.prompt}
        if role.tools is not None:
            definition["tools"] = list(role.tools)
        if role.disallowed_tools:
            definition["disallowedTools"] = list(role.disallowed_tools)
        spec["extra"] = ["--agents", json.dumps({role.name: definition}), "--agent", role.name]
        if role.effort:
            spec["extra"] += ["--effort", role.effort]
        spec["permission_mode"] = role.permission_mode
    elif agent == "codex":
        spec["extra"] = ["-c", "developer_instructions=" + _toml_string(role.prompt)]
        if role.read_only:
            spec["extra"] += ["-s", "read-only"]
    elif agent == "opencode":
        definition = {"description": role.description or f"The {role.name} role", "mode": "primary",
                      "prompt": role.prompt}
        permission = {}
        if not any(role.allows(t) for t in WRITE_TOOLS):
            permission["edit"] = "deny"
        for tool, key in (("Bash", "bash"), ("WebFetch", "webfetch"), ("WebSearch", "websearch")):
            if not role.allows(tool):
                permission[key] = "deny"
        if permission:
            definition["permission"] = permission
        try:
            content = json.loads(environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except ValueError:
            content = {}
        if not isinstance(content, dict):
            content = {}
        agents = content.get("agent") if isinstance(content.get("agent"), dict) else {}
        content["agent"] = {**agents, opencode_name(role): definition}
        spec["env"] = {"OPENCODE_CONFIG_CONTENT": json.dumps(content)}
        spec["extra"] = ["--agent", opencode_name(role)]
    else:
        raise RoleError(f"unknown agent {agent}")
    return spec
