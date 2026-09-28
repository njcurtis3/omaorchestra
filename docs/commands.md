# Commands

<!-- sections -->
**Sections:** [At a glance](#at-a-glance) · [omaorchestra daemon](#omaorchestra-daemon) · [omaorchestra ping](#omaorchestra-ping) · [omaorchestra ls](#omaorchestra-ls) · [omaorchestra focus](#omaorchestra-focus) · [omaorchestra app](#omaorchestra-app) · [omaorchestra watch](#omaorchestra-watch) · [omaorchestra top](#omaorchestra-top) · [omaorchestra run](#omaorchestra-run) · [omaorchestra queue](#omaorchestra-queue) · [omaorchestra handoff](#omaorchestra-handoff) · [omaorchestra permissions](#omaorchestra-permissions) · [omaorchestra spend](#omaorchestra-spend) · [omaorchestra mcp](#omaorchestra-mcp) · [omaorchestra provider](#omaorchestra-provider) · [omaorchestra models](#omaorchestra-models) · [omaorchestra worktree](#omaorchestra-worktree) · [omaorchestra recipe](#omaorchestra-recipe) · [omaorchestra fleet](#omaorchestra-fleet) · [omaorchestra role](#omaorchestra-role) · [omaorchestra stop](#omaorchestra-stop) · [omaorchestra dismiss](#omaorchestra-dismiss) · [omaorchestra hook](#omaorchestra-hook) · [omaorchestra hooks](#omaorchestra-hooks) · [omaorchestra remote](#omaorchestra-remote) · [omaorchestra history](#omaorchestra-history) · [omaorchestra resume](#omaorchestra-resume) · [omaorchestra approvals](#omaorchestra-approvals) · [omaorchestra approve](#omaorchestra-approve) · [omaorchestra deny](#omaorchestra-deny) · [omaorchestra away](#omaorchestra-away) · [omaorchestra config](#omaorchestra-config) · [omaorchestra service](#omaorchestra-service) · [omaorchestra setup](#omaorchestra-setup) · [omaorchestra teardown](#omaorchestra-teardown)
<!-- /sections -->

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
| [`recipe`](#omaorchestra-recipe) | named multi-step chains: plan-then-build, build-then-review, your own |
| [`fleet`](#omaorchestra-fleet) | fleet runs: a scout, an architect, builders and reviewers on one goal, with gates for you |
| [`role`](#omaorchestra-role) | roles an agent can run as: scout, architect, builder, reviewer, integrator, yours |
| [`stop`](#omaorchestra-stop) | stop a session's agent process (asks first) |
| [`dismiss`](#omaorchestra-dismiss) | remove a session from the list (it returns if the agent reports again) |
| [`hook`](#omaorchestra-hook) | receive an agent hook event on stdin |
| [`hooks`](#omaorchestra-hooks) | manage the hooks agents report to omaorchestra with |
| [`remote`](#omaorchestra-remote) | push notifications to your phone (ntfy) |
| [`history`](#omaorchestra-history) | ended sessions: what each did, how long it took, what it cost |
| [`resume`](#omaorchestra-resume) | reopen an ended session's conversation in its folder, in a new terminal |
| [`approvals`](#omaorchestra-approvals) | permission prompts waiting for a remote answer (while you are away) |
| [`approve`](#omaorchestra-approve) | allow one waiting permission prompt (just this request) |
| [`deny`](#omaorchestra-deny) | refuse one waiting permission prompt |
| [`away`](#omaorchestra-away) | whether you are away (pushes and remote answers happen only then): show or set the mode |
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
omaorchestra app [--check] [--session SESSION] [--fleet FLEET]
```

| Argument | Meaning |
|---|---|
| `--check` | load the app offscreen, report whether it reaches the daemon, and exit |
| `--session SESSION` | open on this session's details (id or prefix) |
| `--fleet FLEET` | open omafleet on this fleet run (id or prefix) |

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
                 [--role ROLE]
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
| `--agent AGENT` | which agent (default: the role's, else claude); one of `claude`, `codex`, `opencode` |
| `--role ROLE` | run as this role: its prompt, tools, model and permission mode (see `role list`) |

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
                       [--role ROLE] [--after AFTER] [--same-worktree] [--no-brief]
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
| `--agent AGENT` | which agent (default: the role's, else claude); one of `claude`, `codex`, `opencode` |
| `--role ROLE` | run as this role: its prompt, tools, model and permission mode (see `role list`) |
| `--after AFTER` | start only once this queued task (or running session) finishes; id or prefix |
| `--same-worktree` | with --after: work in that task's worktree and branch, not a new one |
| `--no-brief` | with --after: do not add a brief of what that task did |

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

### `omaorchestra worktree review`

Queue an agent to review a worktree's changes; the verdict shows in `worktree list`.

```
omaorchestra worktree review [--agent {claude,codex,opencode}] [--model MODEL]
                             worktree
```

| Argument | Meaning |
|---|---|
| `worktree` | session id (or prefix), branch, or path |
| `--agent AGENT` | the reviewer (default claude); one of `claude`, `codex`, `opencode` |
| `--model MODEL` | the reviewer's model (a different one from the builder's is a good idea) |

## `omaorchestra recipe`

Named multi-step chains: plan-then-build, build-then-review, your own.

```
omaorchestra recipe <command> ...
```

### `omaorchestra recipe list`

Every recipe.

```
omaorchestra recipe list
```

### `omaorchestra recipe show`

A recipe's steps.

```
omaorchestra recipe show name
```

| Argument | Meaning |
|---|---|
| `name` | the recipe |

### `omaorchestra recipe run`

Queue a recipe's steps as a chain.

```
omaorchestra recipe run [--in DIR] [--model MODEL] [--provider PROVIDER] [--worktree]
                        [--no-worktree] [--paused]
                        name task
```

| Argument | Meaning |
|---|---|
| `name` | the recipe (see `recipe list`) |
| `task` | what to do |
| `--in DIR` | folder to work in (default: here) |
| `--model MODEL` | model for its steps, where the recipe names none |
| `--provider PROVIDER` | run through this API provider instead of the subscription |
| `--worktree` | work in a separate git worktree (default: tasks.isolate_with_worktrees) |
| `--no-worktree` | work in the folder itself |
| `--paused` | add its first step paused |

## `omaorchestra fleet`

Fleet runs: a scout, an architect, builders and reviewers on one goal, with gates for you.

```
omaorchestra fleet <command> ...
```

### `omaorchestra fleet run`

Start a fleet run on a goal; it waits for you at the plan gate.

```
omaorchestra fleet run [--in DIR] [--fleet FLEET] [--shape {single-loop,diamond}]
                       [--budget BUDGET]
                       goal
```

| Argument | Meaning |
|---|---|
| `goal` | what the run should achieve |
| `--in DIR` | folder to work in (default: here) |
| `--fleet FLEET` | which fleet (see `fleet templates`; default auto) |
| `--shape SHAPE` | force the shape, whatever the architect says; one of `single-loop`, `diamond` |
| `--budget BUDGET` | US$ this run may spend (API-equivalent); replaces the fleet's |

### `omaorchestra fleet list`

Current runs (not over, or ended this week).

```
omaorchestra fleet list [--all] [--outside] [--json]
```

| Argument | Meaning |
|---|---|
| `--all` | every run |
| `--outside` | also runs from elsewhere, read-only: graph_agents ([fleets] watch) and Claude agent teams |
| `--json` | machine-readable output |

### `omaorchestra fleet show`

A run: the plan at its gate, else the board (a row per slice).

```
omaorchestra fleet show [--activity [N]] [--json] run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix (graph_agents:<run> or team:<name> for an outside one) |
| `--activity N` | also its last N events (default 20) |
| `--json` | machine-readable output |

### `omaorchestra fleet approve`

Approve the gate the run waits at (the plan, or the merge).

```
omaorchestra fleet approve [--gate {plan,merge}] [--note NOTE] run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `--gate GATE` | which gate (default: the one it waits at); one of `plan`, `merge` |
| `--note NOTE` | a note kept with the approval |

### `omaorchestra fleet send-back`

Send the plan back to a new architect, with what to change.

```
omaorchestra fleet send-back run note
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `note` | what should change |

### `omaorchestra fleet drop`

Leave a slice out of the plan before approving it.

```
omaorchestra fleet drop run slice
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `slice` | the slice's id (s1, s2...) |

### `omaorchestra fleet shape`

Run the plan as a single loop or a diamond (a diamond is still checked).

```
omaorchestra fleet shape run {single-loop,diamond}
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `shape` | one of single-loop, diamond |

### `omaorchestra fleet accept-files`

Accept the files a builder changed outside its slice, saying why.

```
omaorchestra fleet accept-files run node reason
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `node` | the builder (builder.s1...) |
| `reason` | why they belong (the reviewer is told) |

### `omaorchestra fleet undo-files`

Send the slice to a new builder to undo the files outside it.

```
omaorchestra fleet undo-files run node
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `node` | the builder (builder.s1...) |

### `omaorchestra fleet retry`

A held run tries again: a new attempt of what held it.

```
omaorchestra fleet retry [--note NOTE] run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `--note NOTE` | what to do differently (goes into its brief) |

### `omaorchestra fleet limits`

Change a run's budget or step limit (a run held by them goes on).

```
omaorchestra fleet limits [--budget BUDGET] [--steps STEPS] run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `--budget BUDGET` | US$ (0: no budget) |
| `--steps STEPS` | nodes the run may start in all |

### `omaorchestra fleet pause`

Hold a run: nothing new starts (agents already working go on).

```
omaorchestra fleet pause run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |

### `omaorchestra fleet resume`

Let a paused run go on.

```
omaorchestra fleet resume run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |

### `omaorchestra fleet cancel`

Stop a run: nothing more starts.

```
omaorchestra fleet cancel run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |

### `omaorchestra fleet close`

Check a finished run against git and close it (its branch stays yours to merge).

```
omaorchestra fleet close [--check] run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix |
| `--check` | only the checks; close nothing |

### `omaorchestra fleet templates`

The fleets a run can use: auto, single-loop, diamond, yours.

```
omaorchestra fleet templates
```

### `omaorchestra fleet report`

A run's postmortem: time and cost per role, send-backs, gates, parallelism.

```
omaorchestra fleet report [--json] run
```

| Argument | Meaning |
|---|---|
| `run` | run id or prefix (graph_agents:<run> for an outside one) |
| `--json` | machine-readable output |

### `omaorchestra fleet stats`

Across runs, per role: agents, cost, working time, how often reviews reject.

```
omaorchestra fleet stats [--days DAYS] [--json]
```

| Argument | Meaning |
|---|---|
| `--days DAYS` | the last N days (0: every run; default 30) |
| `--json` | machine-readable output |

## `omaorchestra role`

Roles an agent can run as: scout, architect, builder, reviewer, integrator, yours.

```
omaorchestra role <command> ...
```

### `omaorchestra role list`

Every role, and where it comes from.

```
omaorchestra role list [--in DIR] [--json]
```

| Argument | Meaning |
|---|---|
| `--in DIR` | include this folder's project roles (default: here) |
| `--json` | machine-readable output |

### `omaorchestra role show`

A role's settings and prompt.

```
omaorchestra role show [--in DIR] [--json] name
```

| Argument | Meaning |
|---|---|
| `name` | the role |
| `--in DIR` | look in this folder's project roles too (default: here) |
| `--json` | machine-readable output |

### `omaorchestra role check`

Report role files that cannot be used.

```
omaorchestra role check [--in DIR]
```

| Argument | Meaning |
|---|---|
| `--in DIR` | check this folder's project roles too (default: here) |

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

## `omaorchestra history`

Ended sessions: what each did, how long it took, what it cost.

```
omaorchestra history [--project PROJECT] [--agent <command>] [--since SINCE] [--search SEARCH] [--outcome <command>] [--limit LIMIT] [--json] <command> ...
```

| Argument | Meaning |
|---|---|
| `--project PROJECT` | only this project (folder name or part of its path) |
| `--agent AGENT` | only this agent; one of `claude`, `codex`, `opencode` |
| `--since SINCE` | only sessions that ended since: 7d, 12h, 2w, or a date (2026-09-01) |
| `--search SEARCH` | only sessions whose task, folder, model or branch mention this |
| `--outcome OUTCOME` | only sessions that ended this way; one of `finished`, `stopped`, `crashed`, `never-started`, `dismissed` |
| `--limit LIMIT` | how many to show (0: all; default 30) |
| `--json` | machine-readable output |

### `omaorchestra history show`

One session in full, with its commits and latest activity.

```
omaorchestra history show [--limit LIMIT] [--json] id
```

| Argument | Meaning |
|---|---|
| `id` | session id (or its first characters) |
| `--limit LIMIT` | how much of its activity to show |
| `--json` | machine-readable output |

### `omaorchestra history stats`

Time and cost per project, agent and model; how often agents waited.

```
omaorchestra history stats [--since SINCE] [--json]
```

| Argument | Meaning |
|---|---|
| `--since SINCE` | only sessions that ended since: 7d, 12h, 2w, or a date |
| `--json` | machine-readable output |

## `omaorchestra resume`

Reopen an ended session's conversation in its folder, in a new terminal.

```
omaorchestra resume id
```

| Argument | Meaning |
|---|---|
| `id` | session id from `omaorchestra history` (or its first characters) |

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

Whether you are away (pushes and remote answers happen only then): show or set the mode.

```
omaorchestra away [--json] [{auto,on,off}]
```

| Argument | Meaning |
|---|---|
| `mode` | auto: away when locked or idle (default); on: away; off: at the desk |
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
