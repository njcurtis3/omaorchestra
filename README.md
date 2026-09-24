# omaorchestra

Agent coordinator and harness interface for [Omarchy](https://omarchy.org/).

Run AI coding agents side by side, see which are working, waiting for you, or
done, and jump into any of them, from the terminal or the Omarchy bar.

> **Status:** early prototype. The daemon tracks Claude Code sessions through
> hooks, starts new ones on a task (now or from a queue); a bar widget and
> the desktop app show them.
> omaorchestra is an independent, third-party project. It is not part of, or
> endorsed by, Omarchy.

## Planned design

```
omaorchestrad (user systemd service)
   ├─ session registry   who is running, where, what state
   ├─ task queue         pending / running / done / failed
   ├─ adapters           one small module per agent CLI
   └─ unix socket API    $XDG_RUNTIME_DIR/omaorchestra.sock (mode 0600)
          ▲                          ▲
   omaorchestra CLI           Omarchy bar widget + panel (QML)
```

See [docs/architecture.md](docs/architecture.md).

## Layout

| Path | Purpose |
|---|---|
| `src/omaorchestra/` | daemon and CLI (Python, standard library only) |
| `src/omaorchestra/app/` | desktop app (PySide6 + QML) |
| `bin/` | entry-point scripts |
| `plugin/` | Omarchy shell bar widget and panel (QML) |
| `scripts/` | development helpers |
| `packaging/` | PKGBUILD and systemd user unit |
| `config.example.toml` | documented configuration |

## Try it

```bash
bin/omaorchestra service install   # run the daemon as a systemd user service
bin/omaorchestra hooks install     # add hooks to ~/.claude/settings.json
bin/omaorchestra ls                # sessions and their state
bin/omaorchestra watch             # follow changes live
```

`service install` writes a user unit to `~/.config/systemd/user/` pointing at
this checkout, then enables and starts it (`--dry-run` shows the unit first).
With a packaged install it enables the packaged unit instead. `service status`
and `service uninstall` do what they say; uninstall only deletes a unit that
omaorchestra wrote. A second daemon exits with status 3, which the unit tells
systemd not to restart on, so starting one by hand never causes a restart
loop. To run it in the foreground instead: `bin/omaorchestra daemon`.

The daemon logs to the journal with proper priorities, one `key=value` line
per event: start and stop, sessions starting, changing state and ending (with
the reason), and bad requests.

```bash
journalctl --user -u omaorchestrad -f            # follow
journalctl --user -u omaorchestrad -p warning    # problems only
```

`daemon --verbose` (or `verbose = true` under `[daemon]` in the config) also
logs every request. Waiting messages are never included in request lines.

`hooks install` merges omaorchestra's hooks into Claude Code's settings,
pointing at this copy of `omaorchestra` by absolute path. It keeps every other
setting and hook, backs the file up first
(`settings.json.omaorchestra-backup-<time>`), refuses to touch a file that is
not valid JSON, and is safe to run again. `hooks install --dry-run` shows the
result without writing; `hooks status` checks it; `hooks uninstall` removes
only omaorchestra's entries. `--settings <file>` targets another settings
file (it also honours `CLAUDE_CONFIG_DIR`).

Each Claude Code session then reports itself:

| Hook | Status |
|---|---|
| `SessionStart`, `Stop` | `idle` |
| `UserPromptSubmit`, `PostToolUse` | `working` |
| `Notification` | `needs-input` (permission prompt or waiting) |
| `SessionEnd` | removed |

The hook command never fails or blocks the agent: if the daemon is down, events
are dropped.

Hooks also report the agent's process, so a session whose agent crashes or is
killed without `SessionEnd` disappears within 30 seconds.

## App

`omaorchestra app` opens the omaorchestra window: a native Qt (PySide6 + QML)
app, not a web page. It follows the current Omarchy theme and font, reconnects
by itself when the daemon restarts, and keeps a single window (launching it
again focuses the open one). Open it from the app launcher, the button at the
top of the bar panel, the Omarchy menu, or a keybinding.

The **Sessions** page lists every session, waiting first: project, title,
git branch, model, the waiting message, and how long it has been in its
current state. Filter by status or by text, switch between a list and a grid,
and use a row's icons to jump to its terminal, copy its path, open its folder
or dismiss it.

Click a session for its details: the latest prompts, replies and tool calls
from its transcript, a timeline of its status changes, and the uncommitted
git changes in its folder (all changes there, not only the agent's). From
there you can focus it, dismiss it, or stop its agent process (after a
confirmation). `omaorchestra app --session <id>` opens the app on a session,
or switches the open window to it. The model and branch come from the
agent's transcript (Claude Code), or from the agent itself when it reports
them.

```bash
sudo pacman -S pyside6        # the app's one dependency
omaorchestra app              # open it
omaorchestra app --check      # load it offscreen and report whether it reaches the daemon
```

PySide6 from pacman is installed for the system Python, so `bin/omaorchestra`
always runs `/usr/bin/python3`; a version manager's python3 earlier on PATH
would not see it. The app's Wayland app id (and Hyprland class) is
`omaorchestra`; [packaging/omarchy/windows.lua](packaging/omarchy/windows.lua)
has a rule to float, center and size it.

## Bar widget

`plugin/omaorchestra.sessions/` is an Omarchy shell plugin. It watches
`~/.local/state/omaorchestra/sessions.json`, which the daemon rewrites on every
change, so it needs no polling and keeps working while the daemon is down.

- The bar shows the count of working sessions, or "N waiting" in the urgent
  colour when an agent is blocked on you.
- Click to open a panel listing each session (waiting first, with the
  permission message), right-click to reload.
- Click a session in the panel to jump to its terminal window. A window that
  is minimized or on a scratchpad is brought to the current workspace first.
- Each session row has icons to copy its path, open its folder, and dismiss
  it. A dismissed session comes back if its agent reports again, so dismiss
  is for sessions whose agent is gone for good (`omaorchestra dismiss <id>`
  does the same from a terminal).

```bash
scripts/dev-install --restart          # plugin, CLI link, desktop entry and icon
omarchy bar put omaorchestra.sessions --before omarchy.agents
```

The shell's hot reload keeps previously compiled QML, so after editing the
widget run `omarchy restart shell` (the `--restart` flag does it).

## Development

```bash
/usr/bin/python3 -m unittest discover tests   # everything (app tests need PySide6)
node --test tests/plugin            # bar widget formatting logic
omaorchestra app --check            # load the app offscreen; fails on QML warnings or no daemon
```

Set `OMAORCHESTRA_SOCKET` and `OMAORCHESTRA_STATE_DIR` to run a throwaway
daemon alongside a real one.

The app tests run offscreen (`QT_QPA_PLATFORM=offscreen`). Besides unit
tests and per-page renders, `tests/test_ui_flows.py` drives the whole window
against a throwaway daemon with simulated clicks and keys (sessions appearing
live, details and Esc, filters, search, saving a setting, reconnecting after
a daemon restart) and fails on any QML warning.

## Notifications

When an agent starts waiting for you, a notification says so ("proj needs
you", with the permission prompt) and has a **Focus** button that jumps to its
terminal. Once you answer in the terminal, the notification closes itself.
When an agent finishes after working at least two minutes, a quieter
"finished" notification says how long it took. Both go through the normal
notification server, so Omarchy's do-not-disturb silences them like any other
app. Turn either off, or change the two-minute threshold, under
`[notifications]` in the config.

## Starting an agent

```bash
omaorchestra run "fix the flaky login test" --in ~/code/app
omaorchestra run "add a changelog entry" --model sonnet -- --add-dir ../docs
```

In the app, **New task** (Ctrl+N) does the same with a form: the task, the
folder (typed, browsed, or picked from folders you have launched in or that
have sessions), the model, and the permission mode; after launching, the app
shows the new session. `run` opens Claude Code on the task in a new terminal window (your default
terminal, the same way Omarchy opens its agent), and the session is in the bar
and the app at once: omaorchestra picks the session id and registers it before
the agent starts. `--model` and anything after `--` go to the agent;
`--permission-mode` sets its permission mode (by default its own setting
applies, so it asks before acting).

Claude Code asks whether to trust a folder the first time it works there, and
does nothing, hooks included, until that is answered. omaorchestra finds the
new agent's process by its session id within a couple of seconds, and if the
agent stays silent it is shown as waiting for you ("Not started yet: its
window may be asking whether to trust this folder"), with a notification.
omaorchestra never answers that question for you. A launch whose agent never
appears is dropped after a minute.

## The queue

Queue tasks to start one after another as agents free up, instead of all at
once:

```bash
omaorchestra queue add "update the dependencies" --in ~/code/app
omaorchestra queue                      # the queue and how many slots are busy
omaorchestra queue up|down <id>         # or: move <id> <position>
omaorchestra queue pause|resume <id>    # resume also retries a failed task
omaorchestra queue run <id>             # start it now, whatever the limit
omaorchestra queue cancel <id>
omaorchestra queue hold|release         # stop starting new tasks, and resume
```

The daemon starts the next task whenever fewer than `tasks.max_parallel`
agents are busy. Busy means working or waiting for you; an idle agent has
finished and does not hold a slot. Every agent counts, including ones you
started yourself. A task that fails to start stays in the queue, marked
failed with the reason. The queue is kept across daemon restarts. In the
app, **Add to queue** on the New task page queues instead of launching, and
the **Queue** page shows and controls it live.

The queue also watches your subscription's rate limits, as Omarchy's agents
widget records them (`~/.local/state/omarchy/agents/usage/`): while any
limit is at or above `tasks.pause_at_usage` (90% by default; 0 turns it
off), no queued task starts, and the queue says why ("Claude Code's Session
(5-hour) limit is at 92%, resets 19:39"). A limit whose reset time has
passed no longer counts, a record over an hour old is ignored, and an old
record is refreshed in the background with Omarchy's own updater. `queue
run` starts a task anyway.

A queued task records the agent's full path and the `PATH` of whoever queued
it, because the daemon runs under systemd, whose `PATH` usually lacks
version-manager folders (mise, asdf). Nothing else from that environment is
kept.

## Worktrees

In a git repository, each task started from omaorchestra gets its own git
worktree and branch (`omaorchestra/<task words>-<id>`, from the commit the
checkout is on), so agents working in parallel never touch each other's
files or your checkout. Worktrees live outside the repository, under
`~/.local/share/omaorchestra/worktrees/`, so nothing shows up in your
`git status`. Uncommitted changes in your checkout are not carried over.
Turn this off per task (`run --no-worktree`, or the switch in New task) or
with `tasks.isolate_with_worktrees = false`.

A worktree outlives its agent: it holds the work until you decide.

```bash
omaorchestra worktree list              # every task worktree and how it stands
omaorchestra worktree diff <id>         # everything done since the task started
omaorchestra worktree merge <id>        # into the branch it started from
omaorchestra worktree remove <id>       # --force to discard unmerged work
```

The app's **Worktrees** page does the same, and a worktree session's Changes
tab shows its commits and diff since it started. Merging happens in your
main checkout and refuses unless it is clean and on the branch the task
started from; a conflicting merge is undone. Removing refuses to lose
uncommitted or unmerged work unless forced. Each new worktree is a new
folder, so Claude Code asks once whether to trust it.

## Stopping an agent

`omaorchestra stop <id>` asks before ending the session's agent process
(`--yes` skips the question). It checks that the PID still belongs to the same
process first, so a reused PID is never signalled. The conversation stays in
the agent's transcript.

## Jumping to a session

```bash
omaorchestra focus            # the session that needs you most (waiting first)
omaorchestra focus 55a4e525   # a session by id or id prefix
```

omaorchestra walks up the process tree from the agent to the nearest process
that owns a Hyprland window. Terminals that run a process per window (foot,
alacritty, kitty) give an exact match; single-process terminals such as
ghostty or `foot --server` own several windows, so the match is a best guess
and `focus` says so. Agents inside tmux or over ssh have no window to find.
It works with both Hyprland's Lua dispatch syntax (0.55 and later) and the
classic one.

## Keybindings and menu

[packaging/omarchy/](packaging/omarchy/) has ready-made snippets:

- `bindings.lua` for `~/.config/hypr/bindings.lua`:
  **SUPER + ALT + A** jumps to the agent that needs you, and
  **SUPER + CTRL + ALT + A** toggles the sessions panel on the focused monitor,
  and **SUPER + SHIFT + CTRL + ALT + A** opens the app. All three are free in a
  stock Omarchy.
- `omarchy-menu.jsonc` for `~/.config/omarchy/extensions/omarchy-menu.jsonc`:
  an **Agents** submenu with App, Sessions, Jump to agent, Daemon log and
  Restart daemon.

`omaorchestra focus --notify` reports a failure (no sessions, no window) as a
notification, since a keybinding has no terminal to print to.

## Providers and models

Beyond your Claude subscription, omaorchestra knows about API providers:
Anthropic's API, OpenAI, OpenRouter and a local Ollama (any of them at a
different endpoint too, such as a proxy).

```bash
omaorchestra provider add openrouter
omaorchestra provider key openrouter    # asks for the key; it goes to the system keyring
omaorchestra provider test openrouter
omaorchestra models --refresh           # every provider's models, with context and prices
```

API keys are stored in the system keyring (Secret Service, via `secret-tool`)
and nowhere else: not in config files, logs, the queue, or this repository.
The provider list itself (ids, kinds, endpoints) is in
`~/.config/omaorchestra/providers.json`. omaorchestra never sends prompts to
a provider; it lists models, checks keys and reads usage, with plain HTTP.
Model lists are cached in the state directory; prices are per million
tokens, from the provider (OpenRouter) or Anthropic's published prices
(Anthropic's models API reports context sizes but not prices). The app's
**Providers** page does the same.

When a task names no model, it uses its folder's default (or that of a
folder above it), then `tasks.default_model`, then the agent's own:

```bash
omaorchestra models default opus --for ~/code/app   # this project
omaorchestra models default sonnet                  # everywhere else
omaorchestra models default                         # what applies here
```

The New task form lists Claude Code's aliases and the Claude models it
accepts, shows the folder's default, and can remember the chosen model for
the folder. Sessions show their model in the bar panel and the app.

Agents can also run through a provider instead of their subscription:
`omaorchestra run "..." --provider openrouter --model anthropic/claude-sonnet-5`
(or **Runs on** in the New task form). Claude Code can run through the
Anthropic API and OpenRouter, with Claude models only; billing then moves to
the provider. See [docs/providers.md](docs/providers.md) for what works, what
it means for billing and terms, and how the key is handled.

```bash
omaorchestra spend          # today's provider spend, subscription limits, what sessions cost
```

A session's cost is its transcript's token usage times the model's price.
For a session run through a provider that is real spend, kept in a daily
ledger; for a subscription session it is only the API-equivalent, since
subscriptions are not billed per token. With `tasks.daily_budget` set,
queued tasks that run through a provider wait once today's provider spend
reaches it (subscription tasks keep going, and subscription limits hold only
subscription tasks). Provider balances come from the provider where it
reports them (OpenRouter's key usage, in credits); Anthropic's own usage and
cost reports need an Admin API key and are not read. The app's **Usage**
page shows the same, and a session's details show its cost.

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

## Configuration

Settings live in `~/.config/omaorchestra/config.toml` (or `$OMAORCHESTRA_CONFIG`).
Every setting is optional; [config.example.toml](config.example.toml) lists them
with their defaults.

```bash
omaorchestra config check                               # validate the file
omaorchestra config show                                # effective settings, defaults included
omaorchestra config set notifications.finished_after 300
omaorchestra config reload                              # or: systemctl --user reload omaorchestrad
omaorchestra config path
```

The app's **Settings** page edits the same file. Saving (from the app or
`config set`) changes only the lines it needs to, so comments and layout
stay; the result is validated first, the previous file is kept as
`config.toml.bak`, and the daemon picks the change up at once without a
restart. A reload that finds a broken file keeps the running settings.

A mistake in the file (bad TOML, an unknown key, a wrong type) is reported
with the key's name rather than ignored. The daemon then exits with status 2
and systemd does not restart it; agent hooks fall back to defaults and keep
working. Personal settings belong in this file, never in the source tree.

The session registry always lives in `~/.local/state/omaorchestra/`, where the
bar widget reads it, so it is deliberately not configurable.

## Requirements

Omarchy on Arch Linux, Python 3.11+. Plugin APIs in Omarchy are still changing,
so a supported Omarchy version will be pinned here once there is a release.

## License

[MIT](LICENSE)
