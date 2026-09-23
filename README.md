# omaorchestra

Agent coordinator and harness interface for [Omarchy](https://omarchy.org/).

Run AI coding agents side by side, see which are working, waiting for you, or
done, and jump into any of them, from the terminal or the Omarchy bar.

> **Status:** early prototype. The daemon tracks Claude Code sessions through
> hooks and a bar widget shows them; there is no task queue yet.
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
scripts/dev-install-plugin --restart          # copy into ~/.config/omarchy/plugins
                                              # and link the CLI into ~/.local/bin
omarchy bar put omaorchestra.sessions --before omarchy.agents
```

The shell's hot reload keeps previously compiled QML, so after editing the
widget run `omarchy restart shell` (the `--restart` flag does it).

## Development

```bash
python -m unittest discover tests   # daemon, registry, hooks
node --test tests/plugin            # bar widget formatting logic
```

Set `OMAORCHESTRA_SOCKET` and `OMAORCHESTRA_STATE_DIR` to run a throwaway
daemon alongside a real one.

## Notifications

When an agent starts waiting for you, a notification says so ("proj needs
you", with the permission prompt) and has a **Focus** button that jumps to its
terminal. Once you answer in the terminal, the notification closes itself.
When an agent finishes after working at least two minutes, a quieter
"finished" notification says how long it took. Both go through the normal
notification server, so Omarchy's do-not-disturb silences them like any other
app. Turn either off, or change the two-minute threshold, under
`[notifications]` in the config.

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
  **SUPER + CTRL + ALT + A** toggles the sessions panel on the focused monitor.
  Both are free in a stock Omarchy.
- `omarchy-menu.jsonc` for `~/.config/omarchy/extensions/omarchy-menu.jsonc`:
  an **Agents** submenu with Sessions, Jump to agent, Daemon log and Restart
  daemon.

`omaorchestra focus --notify` reports a failure (no sessions, no window) as a
notification, since a keybinding has no terminal to print to.

## Configuration

Settings live in `~/.config/omaorchestra/config.toml` (or `$OMAORCHESTRA_CONFIG`).
Every setting is optional; [config.example.toml](config.example.toml) lists them
with their defaults.

```bash
omaorchestra config check   # validate the file
omaorchestra config show    # effective settings, defaults included
omaorchestra config path
```

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
