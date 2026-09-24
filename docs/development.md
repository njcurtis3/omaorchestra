# Development

How omaorchestra is built, tested and released. The design notes and the
daemon's socket protocol are in [architecture.md](architecture.md).

## Design

```
omaorchestrad (user systemd service)
   ├─ session registry   who is running, where, what state
   ├─ task queue         pending / running / done / failed
   ├─ adapters           one small module per agent CLI
   └─ unix socket API    $XDG_RUNTIME_DIR/omaorchestra.sock (mode 0600)
          ▲                          ▲
   omaorchestra CLI           Omarchy bar widget + panel (QML)
```

See [docs/architecture.md](architecture.md).

## Layout

| Path | Purpose |
|---|---|
| `src/omaorchestra/` | daemon and CLI (Python, standard library only) |
| `src/omaorchestra/app/` | desktop app (PySide6 + QML) |
| `bin/` | entry-point scripts |
| `plugin/` | Omarchy shell bar widget and panel (QML) |
| `scripts/` | dev install, screenshots, AUR publishing |
| `packaging/` | PKGBUILD, systemd user unit, desktop entry, Omarchy snippets |
| `config.example.toml` | documented configuration |

## Tests

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

## Continuous integration

[.github/workflows/test.yml](../.github/workflows/test.yml) runs on every push
and pull request, in an `archlinux` container with the same Python and
PySide6 Omarchy gets: the unit and app tests, the bar widget's node tests,
and `app --check` against a live daemon. To approximate it locally, run the
suite in a bare environment:

```bash
env -i PATH=/usr/bin:/bin HOME=$(mktemp -d) LANG=C.UTF-8 /usr/bin/python3 -m unittest discover tests
```

## Screenshots

`scripts/screenshots` renders the README's screenshots into
`docs/screenshots/`: the app, offscreen, against a throwaway daemon holding
made-up sessions, in the current Omarchy theme. Nothing from your own
sessions, folders or keys appears in them.

## Releasing

1. Bump the version in `src/omaorchestra/__init__.py`,
   `plugin/omaorchestra.sessions/manifest.json` and `packaging/PKGBUILD`,
   then `cd packaging && makepkg --printsrcinfo > .SRCINFO`
   (`tests/test_packaging.py` fails until all four agree).
2. Build and check the package from the checkout:
   `makepkg -f` in a copy of `packaging/` whose `source` points at
   `git+file://<checkout>#branch=main`.
3. Tag and push: `git tag -a v<version> -m v<version> && git push origin v<version>`.
4. `scripts/aur-publish` pushes `PKGBUILD` and `.SRCINFO` to the AUR (it
   needs an AUR account with an SSH key, and refuses until the tag is on
   GitHub).

`scripts/dev-install --restart` installs a checkout for everyday use instead
of the package (the plugin, a CLI link, the desktop entry); the shell keeps
compiled QML, so `--restart` reloads it after widget changes.
