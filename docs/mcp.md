# MCP servers and permissions

## Quick start

Add a server once and install it for the agents you use; its secret goes to
the keyring, not into any agent's config:

```bash
omaorchestra mcp add db --agent claude --agent codex --secret-env DB_PASS -- db-mcp --read-only
omaorchestra mcp check db                 # start it, do the handshake, list its tools
omaorchestra mcp list                     # every server every agent has, per scope
```

Give a task exactly the servers it needs, and no others:

```bash
omaorchestra mcp profile set data db
omaorchestra run "check last night's numbers" --mcp-profile data
```

Let your agents see each other and suggest work (only you can start it):

```bash
omaorchestra mcp add omaorchestra --agent claude -- omaorchestra mcp serve
```

## MCP servers

One view of the MCP servers configured for Claude Code (user, local and
project scopes), Codex and opencode, and one place to manage them:

```bash
omaorchestra mcp list                   # every server, per agent and scope (values never shown)
omaorchestra mcp check                  # start each, do the MCP handshake, list its tools
omaorchestra mcp add db --agent claude --agent codex --secret-env DB_PASS -- db-mcp --read-only
omaorchestra mcp add gh --agent claude --url https://example.com/mcp --secret-header Authorization
omaorchestra mcp managed | disable <name> | enable <name> | remove <name>
```

Secrets go to the system keyring, never into an agent's config: a stdio
server is installed as `omaorchestra mcp exec <name>`, which adds its secrets
and starts the real server, and an HTTP server's secret headers reach Claude
Code through a `headersHelper` (Codex and opencode have no equivalent, so
they only get HTTP servers without secret headers). Claude Code and Codex
are changed through their own `mcp` commands, opencode by editing
`opencode.json`; each agent's config is backed up first. Claude Code's
project scope (`.mcp.json`, a file teams share) is listed but never written.
The inventory reads local configuration only; claude.ai connectors live in
your account and do not appear.

Health checks run a server as an agent would, in an empty temporary folder
with a minimal environment and a 20-second limit, then stop it.

### Profiles

A profile is a named set of managed servers; a task started with one gets
exactly those servers instead of the agent's own (for Claude Code, through
`--mcp-config` and `--strict-mcp-config`, with a config file only you can
read and no secrets in it). `none` is built in.

```bash
omaorchestra mcp add db --no-install -- db-mcp        # for profiles only
omaorchestra mcp profile set data db
omaorchestra run "check last night's numbers" --mcp-profile data
```

The New task form has the same choice, and queued tasks keep theirs.

### omaorchestra as an MCP server

`omaorchestra mcp serve` lets an agent see the other agents and the queue
(`list_sessions`, `get_session` with recent activity, `list_queue`) and
suggest work with `queue_task`, which always adds the task paused and
notifies you: an agent can propose work, only you can start it. To offer it
to Claude Code: `omaorchestra mcp add omaorchestra --agent claude -- omaorchestra mcp serve`.

## Permissions

```bash
omaorchestra permissions
```

shows what agents may do without asking, read from their own settings and
never changed: Claude Code's allow, ask and deny rules and default mode at
every level (managed, user, each known project), with MCP tool rules grouped
per server and the MCP server allow and deny lists; Codex's approval policy
and sandbox; opencode's permission settings. It also shows a record of each
time an agent waited for your approval, with the prompt and how it ended
(continued, stopped waiting, or the session ended) and how long you took.
The app's **MCP** and **Permissions** pages show the same.
