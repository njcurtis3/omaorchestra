# Commands

Every `omaorchestra` command, with its options. `omaorchestra <command> --help`
prints the same from the command line.

This page is generated from the CLI itself by `scripts/commands`; edit the
help text in `src/omaorchestra/__main__.py`, not this file.

Anything after `--` goes on unchanged: to the agent with `run` and
`queue add`, and as the server's command with `mcp add`.

## At a glance

| Command | What it does |
|---|---|
| [`daemon`](#omaorchestra-daemon) | run the coordinator daemon |
| [`ping`](#omaorchestra-ping) | check whether the daemon is running |
| [`ls`](#omaorchestra-ls) | list agent sessions |
| [`focus`](#omaorchestra-focus) | focus a session's terminal window |
| [`app`](#omaorchestra-app) | open the omaorchestra app window |
| [`watch`](#omaorchestra-watch) | print session changes as they happen |
| [`top`](#omaorchestra-top) | sessions and the queue in the terminal, sized for a phone over SSH; keys or taps |
| [`run`](#omaorchestra-run) | start an agent on a task in a new terminal window |
| [`queue`](#omaorchestra-queue) | tasks waiting for a free agent slot |
| [`handoff`](#omaorchestra-handoff) | start another agent (or model) on a session's work, with a brief |
| [`permissions`](#omaorchestra-permissions) | what agents may do without asking, and what they asked |
| [`spend`](#omaorchestra-spend) | provider spend today, subscription limits, and what sessions cost |
| [`mcp`](#omaorchestra-mcp) | MCP servers across Claude Code, Codex and opencode |
| [`provider`](#omaorchestra-provider) | model providers (API keys live in the system keyring) |
| [`models`](#omaorchestra-models) | models from Claude Code and your providers |
| [`worktree`](#omaorchestra-worktree) | task worktrees: list, review, merge, remove |
| [`stop`](#omaorchestra-stop) | stop a session's agent process (asks first) |
| [`dismiss`](#omaorchestra-dismiss) | remove a session from the list (it returns if the agent reports again) |
| [`hook`](#omaorchestra-hook) | receive an agent hook event on stdin |
| [`hooks`](#omaorchestra-hooks) | manage the hooks agents report to omaorchestra with |
| [`remote`](#omaorchestra-remote) | push notifications to your phone (ntfy) |
| [`approvals`](#omaorchestra-approvals) | permission prompts waiting for a remote answer (while you are away) |
| [`approve`](#omaorchestra-approve) | allow one waiting permission prompt (just this request) |
| [`deny`](#omaorchestra-deny) | refuse one waiting permission prompt |
| [`away`](#omaorchestra-away) | push only while you are away: show or set the mode |
| [`config`](#omaorchestra-config) | inspect the configuration |
| [`service`](#omaorchestra-service) | run the daemon as a systemd user service |
| [`setup`](#omaorchestra-setup) | wire omaorchestra into this desktop: service, hooks, bar widget, keybindings, menu |
| [`teardown`](#omaorchestra-teardown) | undo setup (keeps settings, state, worktrees and keys) |

Global options: `--version`, `--help`.

## `omaorchestra daemon`

Run the coordinator daemon.

```
omaorchestra daemon [-v]
```

| Argument | Meaning |
|---|---|
| `-v, --verbose` | log every request |

## `omaorchestra ping`

Check whether the daemon is running.

```
omaorchestra ping
```

## `omaorchestra ls`

List agent sessions.

```
omaorchestra ls [--json]
```

| Argument | Meaning |
|---|---|
| `--json` | machine-readable output |

## `omaorchestra focus`

Focus a session's terminal window.

```
omaorchestra focus [--notify] [session]
```

| Argument | Meaning |
|---|---|
| `session` | session id or prefix (default: the one that needs you) |
| `--notify` | report failures as a notification (for keybindings) |

## `omaorchestra app`

Open the omaorchestra app window.

```
omaorchestra app [--check] [--session SESSION]
```

| Argument | Meaning |
|---|---|
| `--check` | load the app offscreen, report whether it reaches the daemon, and exit |
| `--session SESSION` | open on this session's details (id or prefix) |

## `omaorchestra watch`

Print session changes as they happen.

```
omaorchestra watch [--json]
```

| Argument | Meaning |
|---|---|
| `--json` | raw protocol messages, one per line |

## `omaorchestra top`

Sessions and the queue in the terminal, sized for a phone over SSH; keys or taps.

```
omaorchestra top
```

## `omaorchestra run`

Start an agent on a task in a new terminal window.

```
omaorchestra run [--in DIR] [--model MODEL] [--permission-mode PERMISSION_MODE]
                 [--worktree] [--no-worktree] [--provider PROVIDER]
                 [--mcp-profile MCP_PROFILE] [--agent {claude,codex,opencode}]
                 task
```

| Argument | Meaning |
|---|---|
| `task` | what the agent should do |
| `--in DIR` | folder to work in (default: here) |
| `--model MODEL` | model to use, passed to the agent |
| `--permission-mode PERMISSION_MODE` | the agent's permission mode (default: its own setting) |
| `--worktree` | work in a separate git worktree (default: tasks.isolate_with_worktrees) |
| `--no-worktree` | work in the folder itself |
| `--provider PROVIDER` | run through this API provider instead of the subscription |
| `--mcp-profile MCP_PROFILE` | only this profile's MCP servers ('none' for none) |
| `--agent AGENT` | which agent (default claude); one of `claude`, `codex`, `opencode` |

## `omaorchestra queue`

Tasks waiting for a free agent slot.

```
omaorchestra queue <command> ...
```

### `omaorchestra queue list`

The queue and how many slots are busy.

```
omaorchestra queue list
```

### `omaorchestra queue add`

Queue a task (anything after -- goes to the agent).

```
omaorchestra queue add [--in DIR] [--model MODEL] [--permission-mode PERMISSION_MODE]
                       [--worktree] [--no-worktree] [--paused] [--provider PROVIDER]
                       [--mcp-profile MCP_PROFILE] [--agent {claude,codex,opencode}]
                       task
```

| Argument | Meaning |
|---|---|
| `task` | what the agent should do |
| `--in DIR` | folder to work in (default: here) |
| `--model MODEL` | model to use, passed to the agent |
| `--permission-mode PERMISSION_MODE` | the agent's permission mode (default: its own setting) |
| `--worktree` | work in a separate git worktree (default: tasks.isolate_with_worktrees) |
| `--no-worktree` | work in the folder itself |
| `--paused` | add it paused |
| `--provider PROVIDER` | run through this API provider instead of the subscription |
| `--mcp-profile MCP_PROFILE` | only this profile's MCP servers ('none' for none) |
| `--agent AGENT` | which agent (default claude); one of `claude`, `codex`, `opencode` |

### `omaorchestra queue cancel`

Remove a task from the queue.

```
omaorchestra queue cancel id
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |

### `omaorchestra queue pause`

Skip it until resumed.

```
omaorchestra queue pause id
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |

### `omaorchestra queue resume`

Let it run again (also retries a failed task).

```
omaorchestra queue resume id
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |

### `omaorchestra queue run`

Start it now, whatever the limit.

```
omaorchestra queue run id
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |

### `omaorchestra queue hold`

Start nothing new until released.

```
omaorchestra queue hold
```

### `omaorchestra queue release`

Let the queue run again.

```
omaorchestra queue release
```

### `omaorchestra queue move`

Put a task at a position (1 = next).

```
omaorchestra queue move id position
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |
| `position` | its new place; 1 runs next |

### `omaorchestra queue up`

Move a task one place up.

```
omaorchestra queue up id
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |

### `omaorchestra queue down`

Move a task one place down.

```
omaorchestra queue down id
```

| Argument | Meaning |
|---|---|
| `id` | queued task id or prefix |

## `omaorchestra handoff`

Start another agent (or model) on a session's work, with a brief.

```
omaorchestra handoff [--agent {claude,codex,opencode}] [--model MODEL]
                     [--provider PROVIDER] [--queue] [--stop]
                     session
```

| Argument | Meaning |
|---|---|
| `session` | session id or prefix |
| `--agent AGENT` | default: tasks.fallback_agent, else claude; one of `claude`, `codex`, `opencode` |
| `--model MODEL` | model for the new agent (default: its own) |
| `--provider PROVIDER` | run the new agent through this API provider |
| `--queue` | queue it instead of starting it now |
| `--stop` | stop the old session once the new one is started |

## `omaorchestra permissions`

What agents may do without asking, and what they asked.

```
omaorchestra permissions [--json] [--limit LIMIT]
```

| Argument | Meaning |
|---|---|
| `--json` | machine-readable output |
| `--limit LIMIT` | how many recent requests to show |

## `omaorchestra spend`

Provider spend today, subscription limits, and what sessions cost.

```
omaorchestra spend [--json] [--offline]
```

| Argument | Meaning |
|---|---|
| `--json` | machine-readable output |
| `--offline` | do not ask providers for their balances |

## `omaorchestra mcp`

MCP servers across Claude Code, Codex and opencode.

```
omaorchestra mcp <command> ...
```

### `omaorchestra mcp list`

Every configured MCP server, per agent and scope.

```
omaorchestra mcp list [--json]
```

| Argument | Meaning |
|---|---|
| `--json` | machine-readable output |

### `omaorchestra mcp add`

Add a server to agents, its secrets to the keyring (stdio: the command after --; HTTP: --url).

```
omaorchestra mcp add [--agent AGENT] [--url URL] [--transport {http,sse}] [--env ENV]
                     [--secret-env SECRET_ENV] [--header HEADER]
                     [--secret-header SECRET_HEADER] [--secrets-stdin] [--no-install]
                     name
```

| Argument | Meaning |
|---|---|
| `name` | a name for the server |
| `--agent AGENT` | claude[:user\|:local:/project], codex, opencode (repeatable; default claude) |
| `--url URL` | an HTTP server's URL |
| `--transport TRANSPORT` | for --url (default http); one of `http`, `sse` |
| `--env ENV` | KEY=VALUE for a stdio server (not secret) |
| `--secret-env SECRET_ENV` | KEY whose value is asked for and kept in the keyring |
| `--header HEADER` | 'Name: value' for an HTTP server (not secret) |
| `--secret-header SECRET_HEADER` | header whose value is asked for and kept in the keyring |
| `--secrets-stdin` | read secret values from stdin, one per line |
| `--no-install` | keep it for profiles only; install in no agent |

### `omaorchestra mcp managed`

Servers omaorchestra manages.

```
omaorchestra mcp managed
```

### `omaorchestra mcp check`

Start servers, do the MCP handshake, list their tools.

```
omaorchestra mcp check [--agent {claude,codex,opencode}] [name]
```

| Argument | Meaning |
|---|---|
| `name` | only this server |
| `--agent AGENT` | only this agent's servers; one of `claude`, `codex`, `opencode` |

### `omaorchestra mcp serve`

Omaorchestra's own MCP server, over stdio (for agents).

```
omaorchestra mcp serve
```

### `omaorchestra mcp profile`

Named sets of managed servers to start tasks with.

```
omaorchestra mcp profile <command> ...
```

#### `omaorchestra mcp profile list`

Every profile and its servers.

```
omaorchestra mcp profile list
```

#### `omaorchestra mcp profile set`

Create or replace a profile.

```
omaorchestra mcp profile set name [servers ...]
```

| Argument | Meaning |
|---|---|
| `name` | profile name |
| `servers` | managed servers in it (none: an empty profile) |

#### `omaorchestra mcp profile remove`

Delete a profile (its servers stay).

```
omaorchestra mcp profile remove name
```

| Argument | Meaning |
|---|---|
| `name` | profile name |

### `omaorchestra mcp remove`

Remove from its agents and forget it, secrets too.

```
omaorchestra mcp remove name
```

| Argument | Meaning |
|---|---|
| `name` | a managed server's name |

### `omaorchestra mcp enable`

Install it in its agents again.

```
omaorchestra mcp enable name
```

| Argument | Meaning |
|---|---|
| `name` | a managed server's name |

### `omaorchestra mcp disable`

Take it out of its agents, keep it here.

```
omaorchestra mcp disable name
```

| Argument | Meaning |
|---|---|
| `name` | a managed server's name |

### `omaorchestra mcp exec`

Start a managed stdio server with its secrets (agents run this).

```
omaorchestra mcp exec name
```

| Argument | Meaning |
|---|---|
| `name` | a managed server's name |

### `omaorchestra mcp headers`

Print a managed HTTP server's headers (Claude's headersHelper).

```
omaorchestra mcp headers name
```

| Argument | Meaning |
|---|---|
| `name` | a managed server's name |

## `omaorchestra provider`

Model providers (API keys live in the system keyring).

```
omaorchestra provider <command> ...
```

### `omaorchestra provider list`

Configured providers.

```
omaorchestra provider list
```

### `omaorchestra provider add`

Add a provider: anthropic, openai, openrouter, ollama.

```
omaorchestra provider add [--id ID] [--base-url BASE_URL]
                          {anthropic,openai,openrouter,ollama}
```

| Argument | Meaning |
|---|---|
| `kind` | which service; one of `anthropic`, `openai`, `openrouter`, `ollama` |
| `--id ID` | a name for it (default: the kind) |
| `--base-url BASE_URL` | a different endpoint (a proxy, a self-hosted Ollama, ...) |

### `omaorchestra provider key`

Store its API key in the system keyring.

```
omaorchestra provider key [--stdin] id
```

| Argument | Meaning |
|---|---|
| `id` | the provider's id (see `provider list`) |
| `--stdin` | read the key from standard input |

### `omaorchestra provider remove`

Forget it and its key.

```
omaorchestra provider remove id
```

| Argument | Meaning |
|---|---|
| `id` | the provider's id (see `provider list`) |

### `omaorchestra provider test`

Check it answers and accepts the key.

```
omaorchestra provider test id
```

| Argument | Meaning |
|---|---|
| `id` | the provider's id (see `provider list`) |

## `omaorchestra models`

Models from Claude Code and your providers.

```
omaorchestra models [--provider PROVIDER] [--refresh] [--json] <command> ...
```

| Argument | Meaning |
|---|---|
| `--provider PROVIDER` | only this provider |
| `--refresh` | fetch the lists again |
| `--json` | machine-readable output |

### `omaorchestra models default`

Show or set the model tasks use when they name none.

```
omaorchestra models default [--for DIR] [--clear] [model]
```

| Argument | Meaning |
|---|---|
| `model` | a model or alias (omit to show the current default) |
| `--for DIR` | set it for this folder (and the folders inside it) |
| `--clear` | remove the default |

## `omaorchestra worktree`

Task worktrees: list, review, merge, remove.

```
omaorchestra worktree <command> ...
```

### `omaorchestra worktree list`

Every task worktree and how it stands.

```
omaorchestra worktree list
```

### `omaorchestra worktree diff`

Everything done since the task started.

```
omaorchestra worktree diff worktree
```

| Argument | Meaning |
|---|---|
| `worktree` | session id (or prefix), branch, or path |

### `omaorchestra worktree merge`

Merge into the branch it started from.

```
omaorchestra worktree merge worktree
```

| Argument | Meaning |
|---|---|
| `worktree` | session id (or prefix), branch, or path |

### `omaorchestra worktree remove`

Delete it (refuses to lose work without --force).

```
omaorchestra worktree remove [--force] worktree
```

| Argument | Meaning |
|---|---|
| `worktree` | session id (or prefix), branch, or path |
| `--force` | discard uncommitted or unmerged work |

## `omaorchestra stop`

Stop a session's agent process (asks first).

```
omaorchestra stop [-y] session
```

| Argument | Meaning |
|---|---|
| `session` | session id or prefix |
| `-y, --yes` | do not ask for confirmation |

## `omaorchestra dismiss`

Remove a session from the list (it returns if the agent reports again).

```
omaorchestra dismiss session
```

| Argument | Meaning |
|---|---|
| `session` | session id or prefix |

## `omaorchestra hook`

Receive an agent hook event on stdin.

```
omaorchestra hook {claude,codex,opencode}
```

| Argument | Meaning |
|---|---|
| `agent` | the agent sending the event; one of `claude`, `codex`, `opencode` |

## `omaorchestra hooks`

Manage the hooks agents report to omaorchestra with.

```
omaorchestra hooks <command> ...
```

### `omaorchestra hooks install`

Add the hooks to Claude Code's settings.json.

```
omaorchestra hooks install [--agent {claude,codex,opencode}] [--settings SETTINGS]
                           [--dry-run] [--command HOOK_COMMAND]
```

| Argument | Meaning |
|---|---|
| `--agent AGENT` | which agent (default claude); one of `claude`, `codex`, `opencode` |
| `--settings SETTINGS` | settings.json to edit (default: ~/.claude/settings.json) |
| `--dry-run` | print the result instead of writing it |
| `--command HOOK_COMMAND` | hook command to use (default: this omaorchestra, by absolute path) |

### `omaorchestra hooks uninstall`

Remove the hooks, leaving other settings alone.

```
omaorchestra hooks uninstall [--agent {claude,codex,opencode}] [--settings SETTINGS]
                             [--dry-run]
```

| Argument | Meaning |
|---|---|
| `--agent AGENT` | which agent (default claude); one of `claude`, `codex`, `opencode` |
| `--settings SETTINGS` | settings.json to edit (default: ~/.claude/settings.json) |
| `--dry-run` | print the result instead of writing it |

### `omaorchestra hooks status`

Show whether the hooks are installed.

```
omaorchestra hooks status [--agent {claude,codex,opencode}] [--settings SETTINGS]
```

| Argument | Meaning |
|---|---|
| `--agent AGENT` | which agent (default claude); one of `claude`, `codex`, `opencode` |
| `--settings SETTINGS` | settings.json to edit (default: ~/.claude/settings.json) |

### `omaorchestra hooks snippet`

Print the hooks block without writing anything.

```
omaorchestra hooks snippet [--agent {claude,codex,opencode}] [--command HOOK_COMMAND]
```

| Argument | Meaning |
|---|---|
| `--agent AGENT` | which agent (default claude); one of `claude`, `codex`, `opencode` |
| `--command HOOK_COMMAND` | hook command to use (default: this omaorchestra, by absolute path) |

## `omaorchestra remote`

Push notifications to your phone (ntfy).

```
omaorchestra remote <command> ...
```

### `omaorchestra remote status`

How pushes are set up.

```
omaorchestra remote status
```

### `omaorchestra remote topic`

Set the ntfy topic (kept in the system keyring).

```
omaorchestra remote topic [--new | --stdin | --show | --clear]
```

| Argument | Meaning |
|---|---|
| `--new` | make up a hard-to-guess topic and print it |
| `--stdin` | read the topic from standard input |
| `--show` | print the stored topic |
| `--clear` | forget the topic |

### `omaorchestra remote token`

Set an ntfy access token, for protected topics.

```
omaorchestra remote token [--stdin | --clear]
```

| Argument | Meaning |
|---|---|
| `--stdin` | read the token from standard input |
| `--clear` | forget the token |

### `omaorchestra remote test`

Send one test notification now.

```
omaorchestra remote test
```

### `omaorchestra remote ssh-key`

An authorized_keys line that lets a key run only `omaorchestra top` (for a phone; see docs/remote.md).

```
omaorchestra remote ssh-key [--add] [--comment COMMENT] [key]
```

| Argument | Meaning |
|---|---|
| `key` | the public key file (default: read it from standard input) |
| `--add` | append it to ~/.ssh/authorized_keys (backed up first) |
| `--comment COMMENT` | a name for the key in authorized_keys (default: the key's own comment) |

## `omaorchestra approvals`

Permission prompts waiting for a remote answer (while you are away).

```
omaorchestra approvals [--json]
```

| Argument | Meaning |
|---|---|
| `--json` | machine-readable output |

## `omaorchestra approve`

Allow one waiting permission prompt (just this request).

```
omaorchestra approve id
```

| Argument | Meaning |
|---|---|
| `id` | the request's id from `omaorchestra approvals` (or a prefix) |

## `omaorchestra deny`

Refuse one waiting permission prompt.

```
omaorchestra deny [--message MESSAGE] id
```

| Argument | Meaning |
|---|---|
| `id` | the request's id from `omaorchestra approvals` (or a prefix) |
| `--message MESSAGE` | what to tell the agent (default: that you denied it remotely) |

## `omaorchestra away`

Push only while you are away: show or set the mode.

```
omaorchestra away [--json] [{auto,on,off}]
```

| Argument | Meaning |
|---|---|
| `mode` | auto: away when locked or idle (default); on: always push; off: never push |
| `--json` | machine-readable output |

## `omaorchestra config`

Inspect the configuration.

```
omaorchestra config <command> ...
```

### `omaorchestra config path`

Print the config file path.

```
omaorchestra config path
```

### `omaorchestra config show`

Print the effective config, defaults included.

```
omaorchestra config show
```

### `omaorchestra config check`

Validate the config file.

```
omaorchestra config check
```

### `omaorchestra config set`

Change a setting, keeping the file's comments.

```
omaorchestra config set setting value
```

| Argument | Meaning |
|---|---|
| `setting` | section.key, e.g. notifications.finished_after |
| `value` | true/false, a number, or a comma-separated list |

### `omaorchestra config reload`

Make the daemon re-read the config.

```
omaorchestra config reload
```

## `omaorchestra service`

Run the daemon as a systemd user service.

```
omaorchestra service <command> ...
```

### `omaorchestra service install`

Enable and start the service.

```
omaorchestra service install [--dry-run]
```

| Argument | Meaning |
|---|---|
| `--dry-run` | show what would be done |

### `omaorchestra service uninstall`

Stop and disable the service.

```
omaorchestra service uninstall
```

### `omaorchestra service status`

Show whether the service is running.

```
omaorchestra service status
```

## `omaorchestra setup`

Wire omaorchestra into this desktop: service, hooks, bar widget, keybindings, menu.

```
omaorchestra setup [--only STEP] [--skip STEP] [--dry-run]
```

| Argument | Meaning |
|---|---|
| `--only STEP` | only this step (repeatable): service, hooks, widget, bindings, menu, launcher, remote |
| `--skip STEP` | skip this step (repeatable); one of `service`, `hooks`, `widget`, `bindings`, `menu`, `launcher`, `remote` |
| `--dry-run` | show what would be done |

## `omaorchestra teardown`

Undo setup (keeps settings, state, worktrees and keys).

```
omaorchestra teardown [--only STEP] [--skip STEP] [--dry-run]
```

| Argument | Meaning |
|---|---|
| `--only STEP` | only this step (repeatable): service, hooks, widget, bindings, menu, launcher, remote |
| `--skip STEP` | skip this step (repeatable); one of `service`, `hooks`, `widget`, `bindings`, `menu`, `launcher`, `remote` |
| `--dry-run` | show what would be done |
