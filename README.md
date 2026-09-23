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
```

`service install` writes a user unit to `~/.config/systemd/user/` pointing at
this checkout, then enables and starts it (`--dry-run` shows the unit first).
With a packaged install it enables the packaged unit instead. `service status`
and `service uninstall` do what they say; uninstall only deletes a unit that
omaorchestra wrote. A second daemon exits with status 3, which the unit tells
systemd not to restart on, so starting one by hand never causes a restart
loop. To run it in the foreground instead: `bin/omaorchestra daemon`.

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

```bash
scripts/dev-install-plugin --restart          # copy into ~/.config/omarchy/plugins
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

## Configuration

Copy `config.example.toml` to `~/.config/omaorchestra/config.toml`. Personal
settings live there, never in the source tree.

## Requirements

Omarchy on Arch Linux, Python 3.11+. Plugin APIs in Omarchy are still changing,
so a supported Omarchy version will be pinned here once there is a release.

## License

[MIT](LICENSE)
