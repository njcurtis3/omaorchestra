# Troubleshooting

<!-- sections -->
**Sections:** [A session never appears](#a-session-never-appears) · [The daemon will not start](#the-daemon-will-not-start) · [The bar widget is missing or stale](#the-bar-widget-is-missing-or-stale) · [Focus does not find the window](#focus-does-not-find-the-window) · [The app does not open](#the-app-does-not-open) · [Queued tasks do not start](#queued-tasks-do-not-start) · [A fleet run does not move](#a-fleet-run-does-not-move) · [Fleet commits carry a Co-Authored-By trailer](#fleet-commits-carry-a-co-authored-by-trailer) · [Keys and MCP secrets](#keys-and-mcp-secrets) · [Notifications do not show](#notifications-do-not-show) · [Removing everything](#removing-everything)
<!-- /sections -->

Start with the daemon's log and a check of each piece:

```bash
omaorchestra ping                               # is the daemon answering?
omaorchestra service status
journalctl --user -u omaorchestrad -n 50        # its recent log
omaorchestra config check
omaorchestra hooks status                       # add --agent codex / --agent opencode
omaorchestra app --check                        # loads the app offscreen, reports QML problems
omaorchestra setup --dry-run                    # what setup would still change
```

## A session never appears

- **Hooks are not installed**, or point at an old path (after moving a
  checkout): `omaorchestra hooks status`, then `omaorchestra setup --only hooks`.
- **Claude Code is waiting on its folder trust question.** It runs no hooks
  until you answer it in its window. A session started from omaorchestra
  shows as waiting ("Not started yet") in the meantime; one started by hand
  appears after you answer.
- **Codex hooks are not trusted yet.** Start `codex` and trust them with
  `/hooks`; Codex skips untrusted hooks silently.
- **The daemon is not running.** Hooks never block or fail an agent, so
  events are simply dropped: `systemctl --user start omaorchestrad`.

A permission prompt does not show in `omaorchestra approvals` or `top`:
remote answers work only in away mode (`omaorchestra away` says whether you
count as away), only for Claude Code, and only with its `PermissionRequest`
hook installed (`omaorchestra hooks status`; `omaorchestra hooks install`
adds it). The daemon logs `approval asked` and how each one ended.

## The daemon will not start

| Log says | Meaning |
|---|---|
| `bad config` (exit status 2) | A mistake in `~/.config/omaorchestra/config.toml`; `omaorchestra config check` names the key. systemd does not retry until you fix it and start it again. |
| `already running` (exit status 3) | Another daemon (often one started by hand with `omaorchestra daemon`) answers on the socket. Stop that one. |
| `cannot listen` | The socket path (`$XDG_RUNTIME_DIR/omaorchestra.sock`) is too long or not writable. |

## The bar widget is missing or stale

- `omarchy bar put omaorchestra.sessions` puts it back on the bar.
- After an upgrade the shell may keep the old widget code: `omarchy restart shell`.
- The widget reads `~/.local/state/omaorchestra/sessions.json`; if the
  daemon has never run, it shows nothing until it does.

## Focus does not find the window

`omaorchestra focus` walks from the agent's process to the window that owns
it. Terminals with one process per window (foot, alacritty, kitty) match
exactly; a single-process terminal (ghostty, `foot --server`) gives a best
guess, and agents inside tmux or over ssh have no window to find.

## The app does not open

- `omaorchestra app` needs PySide6: `sudo pacman -S pyside6`. It runs with
  `/usr/bin/python3`, where pacman installs it; a mise or pyenv Python does
  not see it.
- A second `omaorchestra app` focuses the open window rather than opening
  another one. If nothing appears, look for it on another workspace.
- The app says **Daemon not running** until the daemon answers, then
  reconnects by itself.

## Queued tasks do not start

`omaorchestra queue` says why. The usual reasons: all `tasks.max_parallel`
slots are busy (an agent waiting for you still counts), the queue is held
(`omaorchestra queue release`), a subscription limit is at or above
`tasks.pause_at_usage`, or today's provider spend reached
`tasks.daily_budget`. `omaorchestra queue run <id>` starts one anyway.

A task that fails to start with "command not found" was queued from a shell
whose `PATH` the daemon cannot see; queue it again from a normal terminal.

## A fleet run does not move

`omaorchestra fleet show <run>` (or the run in omafleet) says what it
waits on. The usual reasons:

- **It waits for you**: at the plan or merge gate, or held (a reply without
  its JSON block, a slice rejected twice, files outside a slice, its
  budget). Each has an answer; see [Fleets](fleets.md#answering).
- **Its next agent is queued, not started**: the queue's limits apply to
  fleet agents too (see above); `omaorchestra queue` shows them as one row
  per run.
- **An agent waits in its window**: the folder-trust question the first
  time Claude Code works in a folder, or a permission prompt. In away mode
  a prompt waits for an answer from your phone (`omaorchestra top`); the
  run is marked as waiting for you.
- **An agent looks stalled**: no sign of life for the fleet's
  `stall_minutes`. Look at its window; stop it, then `fleet retry`.

An answer refused with "inside an agent" was sent from an agent's own
terminal: answer from yours, the app, or `top`.

## Fleet commits carry a Co-Authored-By trailer

Claude Code adds its attribution trailer to commits itself, and a role's
instructions cannot stop it. Set `"attribution": {"commit": "", "pr": ""}`
in `~/.claude/settings.json`; see [Fleets](fleets.md#roles).

## Keys and MCP secrets

`provider key` and `mcp add --secret-*` need a Secret Service keyring
(GNOME Keyring on Omarchy) and `secret-tool` (`libsecret`). "The keyring
did not answer" means it is not running or not unlocked in this session.

## Notifications do not show

Omarchy's do-not-disturb silences omaorchestra like any other app. Check
`[notifications]` in the config too: `waiting = false` and `finished_after = 0`
turn them off.

Phone pushes: `omaorchestra remote` shows whether push is on and a topic is
stored, and `omaorchestra remote test` sends one and reports any error (no
network, the server wants a token, rate limited). The daemon logs `push
failed` once when pushes start failing (`journalctl --user -u omaorchestrad`)
and `push working again` when they recover.

No pushes although push is on: you are probably counted as at the desk.
`omaorchestra away` says which, and why. In `auto` it needs to read the
lock screen (`omarchy-shell lock isLocked`) and idle time (the compositor's
ext-idle-notify protocol, over `WAYLAND_DISPLAY`); if it cannot, it says so
and the daemon logs `idle detection unavailable`. The service gets
`WAYLAND_DISPLAY` from the session (`systemctl --user show-environment`);
`omaorchestra away on` pushes regardless.

## Removing everything

`omaorchestra teardown` undoes `setup` and keeps your settings, state,
worktrees and keys; it prints where each lives. See the
[README](../README.md#uninstall).
