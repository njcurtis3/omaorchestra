# User guide

Everything omaorchestra does, feature by feature. Installing and setting it
up is in the [README](../README.md); every command and option is listed in
[Commands](commands.md).

## The daemon and hooks

`omaorchestra setup` does both steps below; they can also be run on their own.

```bash
omaorchestra service install   # run the daemon as a systemd user service
omaorchestra hooks install     # add hooks to ~/.claude/settings.json
omaorchestra ls                # sessions and their state
omaorchestra watch             # follow changes live
```

`service install` writes a user unit to `~/.config/systemd/user/` pointing at
a source checkout, then enables and starts it (`--dry-run` shows the unit first).
With a packaged install it enables the packaged unit instead. `service status`
and `service uninstall` do what they say; uninstall only deletes a unit that
omaorchestra wrote. A second daemon exits with status 3, which the unit tells
systemd not to restart on, so starting one by hand never causes a restart
loop. To run it in the foreground instead: `omaorchestra daemon`.

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
| `PermissionRequest`, `Notification` | `needs-input` (permission prompt or waiting) |
| `SessionEnd` | removed |

The hook command never fails or blocks the agent: if the daemon is down, events
are dropped. The one wait is a permission prompt while you are away (see
[Answering permission prompts remotely](#answering-permission-prompts-remotely)),
and even then the prompt stays up in the terminal and answers as usual.

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

PySide6 from pacman is installed for the system Python, so `omaorchestra`
always runs `/usr/bin/python3`; a version manager's python3 earlier on PATH
would not see it (`OMAORCHESTRA_PYTHON` picks another interpreter). The app's Wayland app id (and Hyprland class) is
`omaorchestra`; [packaging/omarchy/windows.lua](../packaging/omarchy/windows.lua)
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

`omaorchestra setup` copies the widget into `~/.config/omarchy/plugins/` and
puts it on the bar; to place it elsewhere:

```bash
omarchy bar move omaorchestra.sessions --before omarchy.agents
```

## In a terminal (and on your phone)

`omaorchestra top` shows sessions and the queue in the terminal, live, the
way the app does. It fits a phone's screen (about 40 columns), so over SSH
from Termius or Blink it is the way to check on agents while you are away.
[From your phone](remote.md) sets that up safely: over Tailscale, with a key
that can run `top` and nothing else.

- Two tabs, **Sessions** (waiting ones first, with what they are asking) and
  **Queue**. Tab or ←/→ switches; ↑/↓ (or j/k) picks; Enter shows everything
  about the picked one.
- On a session asking for permission while you are away: **y** allows that
  one request (after showing it in full and a yes), **x** refuses it.
- On sessions: **d** dismisses, **s** stops the agent (after a yes), **h**
  hands its work to another agent.
- On the queue: **p** pauses or resumes a task (resume also retries a failed
  one), **x** cancels it (after a yes), **H** holds or releases the whole
  queue.
- **n** queues a new task: type it, then the folder (the picked session's, or
  the last one you used), then pick the agent.
- **a** switches [away mode](#away-mode); the top right
  says whether you count as away.
- **q** quits.

Every key is also a button along the bottom, and rows, tabs and buttons
respond to taps in terminals that pass them through as mouse clicks, so a
phone keyboard is mostly needed to type a task. Focusing a terminal window
is left out on purpose: it would happen on a desk nobody is at.

## Notifications

When an agent starts waiting for you, a notification says so ("proj needs
you", with the permission prompt) and has a **Focus** button that jumps to its
terminal. Once you answer in the terminal, the notification closes itself.
When an agent finishes after working at least two minutes, a quieter
"finished" notification says how long it took. Both go through the normal
notification server, so Omarchy's do-not-disturb silences them like any other
app. Turn either off, or change the two-minute threshold, under
`[notifications]` in the config.

### On your phone

omaorchestra can also push to your phone through [ntfy](https://ntfy.sh), an
open-source notification service with Android and iOS apps. It only sends:
each push is one HTTPS request from the daemon, and nothing listens for
connections.

```bash
omaorchestra remote topic --new         # make a hard-to-guess topic; prints it
omaorchestra config set remote.push true
omaorchestra remote test                # one test notification
```

Subscribe to the printed topic in the ntfy app (server `https://ntfy.sh`
unless you changed it). On ntfy.sh, anyone who knows a topic can read it, so
the topic is kept in the system keyring rather than the config file, and
`remote topic --new` makes one nobody will guess. For a self-hosted ntfy
server, set `remote.server` and, if its topics need one, store an access token
with `omaorchestra remote token`. `omaorchestra remote` shows how things
stand.

Pushes go out when an agent needs you, when long work finishes (after
`notifications.finished_after`), when a queued task fails to start, when a
busy agent reaches its usage limit, and when the queue is held back; pick
which with `remote.events`. What they say is `remote.content`:

| Level | A push carries |
|---|---|
| `minimal` (default) | the project folder's name and what happened |
| `summary` | also the task's title |
| `full` | also the agent's question, which can quote commands and code |

Prompts and code leave the machine only at `full`.

#### Away mode

Pushes go out, and permission prompts can be
[answered remotely](#answering-permission-prompts-remotely), only while you
are away, so your phone stays quiet while you sit at the desk:

```bash
omaorchestra away           # the mode, and whether you count as away now
omaorchestra away auto      # away once the screen locks, or after a while without input (default)
omaorchestra away on        # away until you switch back
omaorchestra away off       # at the desk until you switch back
```

In `auto`, locking the screen (Super+Ctrl+L, or Omarchy locking it for you)
makes you away at once; so do `remote.away_after` minutes (10 by default)
without keyboard or mouse input. Anything that keeps the screen awake, such
as a playing video, keeps you at the desk too. The lock screen is checked
again right before each push, so locking and walking off loses nothing.
`remote test` always sends.

The app's sidebar has an **Auto / Away / Here** switch, the bar widget's
panel an auto / on / off one, and `omaorchestra top` switches with **a**.
While you are away the bar shows 󰄜 beside its count. The switches show
whenever being away changes something (push or remote answers on, which is
the default), and the mode is remembered across restarts.

#### Answering permission prompts remotely

While you are away, a Claude Code permission prompt ("Allow Bash: npm
test?") can be answered from your phone as well as at the terminal:

```bash
omaorchestra approvals          # prompts waiting for an answer, with their ids
omaorchestra approve a1b2c3     # allow that one request
omaorchestra deny a1b2c3        # refuse it (--message tells the agent why)
```

or with **y** and **x** in [`omaorchestra top`](#in-a-terminal-and-on-your-phone),
which is the way from a phone over SSH ([From your phone](remote.md)).

- Only in away mode. At the desk the hook returns at once and nothing
  changes.
- The terminal prompt stays up while omaorchestra waits, and whichever
  answers first wins.
- One request at a time: an allow is for that request only and never adds a
  rule, so the next one asks again.
- A request stays answerable for `remote.answer_wait` seconds (600 by
  default), or until it is answered at the terminal or the session moves on.
- Each remote request is recorded with how it ended and where the answer
  came from (`omaorchestra permissions` shows "allowed from top over SSH
  from 100.x.y.z").
- An answer sent from inside an agent (a process under `claude`, `codex` or
  `opencode`) is refused, so an agent cannot approve its own or another
  session's requests in passing. It is a guard, not a wall: agents run as
  you, and one set on it could get around it. Your agents' permission modes
  remain what limits them.

This needs Claude Code's `PermissionRequest` hook, which `omaorchestra setup`
(or `omaorchestra hooks install`) adds; `hooks status` says if it is missing.
In Claude Code's auto mode, only the requests its classifier passes to you
become prompts, so fewer reach your phone. Codex and opencode report their
permission prompts but cannot be answered remotely yet. Turn the whole thing
off with `omaorchestra config set remote.answer_prompts false`.

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

## Agents

Claude Code, Codex and opencode each have an adapter: how to start it, how
it reports its state, and how that reporting is installed.

| | Reports through | Install | Notes |
|---|---|---|---|
| Claude Code | hooks in `~/.claude/settings.json` | `omaorchestra hooks install` | everything: routing, MCP profiles, costs from its transcript |
| Codex | hooks in `~/.codex/hooks.json` | `omaorchestra hooks install --agent codex` | Codex runs them only once you trust them (`/hooks` in Codex) |
| opencode | a plugin, `~/.config/opencode/plugins/omaorchestra.js` | `omaorchestra hooks install --agent opencode` | |

`omaorchestra run --agent codex|opencode` (or **Agent** in New task) starts
the others. They cannot be told a session id up front, so a launch carries
`OMAORCHESTRA_LAUNCH_ID` in the agent's environment: the daemon finds the
agent's process by it, and when the agent first reports, the placeholder
session becomes the agent's own. Provider routing and MCP profiles are
Claude Code only for now.

### Handing work to another agent

```bash
omaorchestra handoff <id> --agent codex            # or --model, --provider; --queue; --stop
```

starts another agent (or model) where the work is, in the same folder or
worktree, with a brief: the original task, where things stand in git, and
the session's latest prompts, replies and tool calls. The app's session
details have a **Hand off** button. With `tasks.fallback_agent` set, a
queued task held by its agent's usage limit starts on the fallback agent
instead, and when an agent reaches its limit while it is working, a
notification offers to hand each of its sessions over (nothing happens
unless you click it).

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

`omaorchestra setup` adds these, from [packaging/omarchy/](../packaging/omarchy/), in a marked block
that `omaorchestra teardown` removes (`--skip bindings --skip menu` leaves them out):

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

## Configuration

Settings live in `~/.config/omaorchestra/config.toml` (or `$OMAORCHESTRA_CONFIG`).
Every setting is optional; [config.example.toml](../config.example.toml) lists them
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
