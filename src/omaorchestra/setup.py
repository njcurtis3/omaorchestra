"""`omaorchestra setup` and `teardown`: wire omaorchestra into a user's
Omarchy desktop, and take it out again.

Each step is idempotent and reports what it did. Edits to the user's own
files (Hyprland bindings, the window rule, the Omarchy menu) go in a marked
block, backed up first, so teardown removes exactly what setup added. A file
that already mentions omaorchestra outside such a block was set up by hand
and is left alone. Teardown never deletes config, state, worktrees or keys.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import adapters, claude_settings, config, service

PLUGIN_ID = "omaorchestra.sessions"
PACKAGED_DATA = Path("/usr/share/omaorchestra")
BEGIN = ">>> omaorchestra (added by `omaorchestra setup`; `omaorchestra teardown` removes it)"
END = "<<< omaorchestra"
STEPS = ("service", "hooks", "widget", "bindings", "menu", "launcher")


class SetupError(Exception):
    pass


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def checkout_root():
    """The source checkout this module runs from, or None when packaged."""
    root = Path(__file__).resolve().parent.parent.parent
    return root if (root / "plugin").is_dir() and (root / "packaging").is_dir() else None


def data_files():
    """(plugin dir, Omarchy snippets dir, desktop entry, icon) for this install."""
    root = checkout_root()
    if root:
        return (root / "plugin" / PLUGIN_ID, root / "packaging" / "omarchy",
                root / "packaging" / "omaorchestra.desktop", root / "packaging" / "icons" / "omaorchestra.svg")
    return PACKAGED_DATA / "plugin" / PLUGIN_ID, PACKAGED_DATA / "omarchy", None, None


# ---------------------------------------------------------------- marked blocks

def snippet_body(path):
    """A packaged snippet without its leading "copy this into ..." comment."""
    lines = Path(path).read_text().splitlines()
    while lines and (lines[0].startswith(("--", "//")) or not lines[0].strip()):
        lines.pop(0)
    return lines


def menu_entries(path):
    """The entries of the menu snippet, each ending in a comma (the menu
    parser drops trailing commas), so they can go first in any object."""
    body = snippet_body(path)
    entries = [line.strip() for line in body if line.strip().startswith('"')]
    return ["  " + (e if e.endswith(",") else e + ",") for e in entries]


def block_pattern(comment):
    return re.compile(rf"^[ \t]*{re.escape(comment)} >>> omaorchestra\b.*?^[ \t]*{re.escape(comment)} {re.escape(END)}[^\n]*\n?",
                      re.M | re.S)


def without_block(text, comment):
    """`text` minus the marked block and the blank line setup put beside it
    (before an appended block, after one at the top of the menu)."""
    match = block_pattern(comment).search(text)
    if not match:
        return text
    start, end = match.start(), match.end()
    if text[:start].endswith("\n\n"):
        start -= 1
    elif text[end:].startswith("\n"):
        end += 1
    return without_block(text[:start] + text[end:], comment)


def render_block(lines, comment, indent=""):
    return "\n".join([f"{indent}{comment} {BEGIN}", *lines, f"{indent}{comment} {END}"]) + "\n"


def with_block(text, lines, comment, after_brace=False):
    """`text` with the marked block set to `lines`: appended at the end, or
    right after the first top-level brace (JSONC)."""
    text = without_block(text, comment)
    if after_brace:
        indent = "  "
        match = re.search(r"^[ \t]*\{[ \t]*\n", text, re.M)
        if not match:
            raise SetupError("cannot find the opening { of the menu object")
        return text[:match.end()] + render_block(lines, comment, indent) + "\n" + text[match.end():]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text.strip() else "") + render_block(lines, comment)


def write_file(path, text):
    """Write atomically, backing up the old file first; returns the backup."""
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.omaorchestra-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.omaorchestra-tmp")
    tmp.write_text(text)
    if backup:
        shutil.copymode(backup, tmp)
    os.replace(tmp, path)
    return backup


def set_block(path, lines, comment, dry_run, after_brace=False, create=None, what="entries"):
    if not path.exists():
        if create is None:
            return [f"{what}: skipped, there is no {path}"]
        before = create
    else:
        before = path.read_text()
    if "omaorchestra" in without_block(before, comment):
        return [f"{what}: {path} already has omaorchestra entries of its own; left alone"]
    after = with_block(before, lines, comment, after_brace)
    if path.exists() and after == before:
        return [f"{what}: {path} already up to date"]
    if dry_run:
        return [f"{what}: would add to {path}:\n" + render_block(lines, comment).rstrip()]
    backup = write_file(path, after)
    return [f"{what}: updated {path}" + (f" (backup: {backup})" if backup else "")]


def remove_block(path, comment, dry_run, what="entries"):
    if not path.exists():
        return []
    before = path.read_text()
    after = without_block(before, comment)
    if after == before:
        return []
    if dry_run:
        return [f"{what}: would remove the omaorchestra block from {path}"]
    backup = write_file(path, after)
    return [f"{what}: removed the omaorchestra block from {path} (backup: {backup})"]


# ---------------------------------------------------------------- steps

def have(command):
    return shutil.which(command) is not None


def omarchy_shell_present():
    return have("omarchy-bar") or (config_home() / "omarchy").is_dir()


def hypr_dir():
    return config_home() / "hypr"


def menu_path():
    return config_home() / "omarchy" / "extensions" / "omarchy-menu.jsonc"


def plugin_dest():
    return config_home() / "omarchy" / "plugins" / PLUGIN_ID


def agent_present(name):
    marker = {"claude": Path.home() / ".claude", "codex": Path.home() / ".codex",
              "opencode": config_home() / "opencode"}.get(name)
    return have(adapters.get(name).binary()) or bool(marker and marker.exists())


def setup_service(binary, dry_run, run):
    if dry_run:
        if service.uses_packaged_unit(binary):
            return [f"service: would enable packaged {service.PACKAGED_UNIT}"]
        unit = service.user_unit_dir() / service.UNIT_NAME
        if unit.exists() and unit.read_text() == service.render_unit(binary):
            return [f"service: {unit} already up to date"]
        return [f"service: would write and enable {unit}"]
    return ["service: " + line for line in service.install(binary, run=run)]


def teardown_service(dry_run, run):
    if dry_run:
        return [f"service: would stop and disable {service.UNIT_NAME}"]
    return ["service: " + line for line in service.uninstall(run=run)]


def setup_hooks(binary, dry_run, agents=None):
    enabled = agents if agents is not None else config.load()["agents"]["enabled"]
    done = []
    for name in enabled:
        adapter = adapters.get(name)
        if not agent_present(name):
            done.append(f"hooks: skipped {adapter.label}, it is not installed")
            continue
        installed, _ = adapter.hooks_status()
        if dry_run:
            done.append(f"hooks: {adapter.label} already reports" if installed else
                        f"hooks: would install {adapter.label} hooks in {adapter._path(None)}")
            continue
        adapter.install_hooks(claude_settings.hook_command(binary, name))
        done.append(f"hooks: {adapter.label} reports to omaorchestra ({adapter._path(None)})")
        if name == "codex":
            done.append("hooks: Codex runs them only once trusted: start codex and use /hooks")
    return done


def teardown_hooks(dry_run):
    done = []
    for adapter in adapters.ADAPTERS.values():
        path = adapter._path(None)
        if not path.exists():
            continue
        if dry_run:
            installed, detail = adapter.hooks_status()
            if installed or detail.startswith("missing"):
                done.append(f"hooks: would remove {adapter.label} hooks from {path}")
            continue
        if adapter.uninstall_hooks() not in (None, "unchanged"):
            done.append(f"hooks: removed {adapter.label} hooks from {path}")
    return done


def setup_widget(dry_run, run):
    source = data_files()[0]
    if not omarchy_shell_present():
        return ["widget: skipped, no Omarchy shell here"]
    dest = plugin_dest()
    files = sorted(p for p in source.iterdir() if p.suffix in (".qml", ".js", ".json"))
    same = dest.is_dir() and all((dest / f.name).exists() and (dest / f.name).read_bytes() == f.read_bytes() for f in files)
    if dry_run:
        return [f"widget: {dest} already up to date" if same else f"widget: would copy the bar widget to {dest}",
                f"widget: would put {PLUGIN_ID} on the bar"]
    done = []
    if not same:
        # A copy, not a symlink: the shell does not follow symlinked plugin dirs.
        if dest.is_symlink():
            dest.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
        done.append(f"widget: copied the bar widget to {dest}")
    if have("omarchy-bar"):
        result = run(["omarchy-bar", "put", PLUGIN_ID], capture_output=True, text=True)
        done.append(f"widget: {PLUGIN_ID} is on the bar" if result.returncode == 0 else
                    f"widget: could not put it on the bar: {(result.stderr or result.stdout).strip()}")
        if not same:
            done.append("widget: run `omarchy restart shell` if the bar shows an older version")
    return done


def teardown_widget(dry_run, run):
    dest = plugin_dest()
    if not dest.exists():
        return []
    manifest = dest / "manifest.json"
    if not dest.is_symlink() and not (manifest.exists() and f'"{PLUGIN_ID}"' in manifest.read_text()):
        return [f"widget: left {dest} alone: it is not omaorchestra's widget"]
    if dry_run:
        return [f"widget: would take {PLUGIN_ID} off the bar and remove {dest}"]
    done = []
    if have("omarchy-plugin"):
        result = run(["omarchy-plugin", "disable", PLUGIN_ID], capture_output=True, text=True)
        if result.returncode == 0:
            done.append(f"widget: took {PLUGIN_ID} off the bar")
    if dest.is_symlink():
        dest.unlink()
    else:
        shutil.rmtree(dest)
    done.append(f"widget: removed {dest}")
    return done


def check_hyprland(run):
    if not (have("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")):
        return []
    result = run(["hyprctl", "configerrors"], capture_output=True, text=True)
    errors = (result.stdout or "").strip()
    if errors and "no errors" not in errors.lower():
        return [f"bindings: Hyprland reports config errors:\n{errors}"]
    return []


def setup_bindings(dry_run, run):
    snippets = data_files()[1]
    done = set_block(hypr_dir() / "bindings.lua", snippet_body(snippets / "bindings.lua"), "--", dry_run,
                     what="bindings")
    done += set_block(hypr_dir() / "hyprland.lua", snippet_body(snippets / "windows.lua"), "--", dry_run,
                      what="window rule")
    if not dry_run and any("updated" in line for line in done):
        done += check_hyprland(run)
    return done


def teardown_bindings(dry_run, run):
    done = remove_block(hypr_dir() / "bindings.lua", "--", dry_run, what="bindings")
    done += remove_block(hypr_dir() / "hyprland.lua", "--", dry_run, what="window rule")
    return done


def setup_menu(dry_run):
    if not omarchy_shell_present():
        return ["menu: skipped, no Omarchy shell here"]
    return set_block(menu_path(), menu_entries(data_files()[1] / "omarchy-menu.jsonc"), "//", dry_run,
                     after_brace=True, create="{\n}\n", what="menu")


def teardown_menu(dry_run):
    return remove_block(menu_path(), "//", dry_run, what="menu")


def launcher_link():
    return Path.home() / ".local" / "bin" / "omaorchestra"


def setup_launcher(binary, dry_run):
    """A checkout needs `omaorchestra` on PATH (the bar, bindings and menu run
    it) and a desktop entry; the package installs both itself."""
    _, _, desktop, icon = data_files()
    if not desktop:
        return []
    done = []
    link = launcher_link()
    on_path = shutil.which("omaorchestra")
    if not (on_path and os.path.realpath(on_path) == os.path.realpath(binary)):
        if link.exists() and not link.is_symlink():
            done.append(f"launcher: left {link} alone: it is not a symlink")
        elif dry_run:
            done.append(f"launcher: would link {link} -> {binary}")
        else:
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.is_symlink():
                link.unlink()
            link.symlink_to(binary)
            done.append(f"launcher: linked {link} -> {binary}")
    for source, dest in ((desktop, data_home() / "applications" / desktop.name),
                         (icon, data_home() / "icons" / "hicolor" / "scalable" / "apps" / icon.name)):
        if dest.exists() and dest.read_bytes() == source.read_bytes():
            continue
        if dry_run:
            done.append(f"launcher: would install {dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            done.append(f"launcher: installed {dest}")
    return done


def teardown_launcher(dry_run):
    done = []
    link = launcher_link()
    if link.is_symlink() and os.path.basename(os.readlink(link)) == "omaorchestra":
        done.append(f"launcher: {'would remove' if dry_run else 'removed'} {link}")
        if not dry_run:
            link.unlink()
    for dest in (data_home() / "applications" / "omaorchestra.desktop",
                 data_home() / "icons" / "hicolor" / "scalable" / "apps" / "omaorchestra.svg"):
        if dest.exists():
            done.append(f"launcher: {'would remove' if dry_run else 'removed'} {dest}")
            if not dry_run:
                dest.unlink()
    return done


# ---------------------------------------------------------------- entry points

def selected(only=(), skip=()):
    for name in list(only) + list(skip):
        if name not in STEPS:
            raise SetupError(f"unknown step {name} (steps: {', '.join(STEPS)})")
    return [s for s in STEPS if (not only or s in only) and s not in skip]


def setup(binary, only=(), skip=(), dry_run=False, run=subprocess.run):
    if not binary:
        raise SetupError("cannot find the omaorchestra binary")
    done = []
    for step in selected(only, skip):
        if step == "service":
            done += setup_service(binary, dry_run, run)
        elif step == "hooks":
            done += setup_hooks(binary, dry_run)
        elif step == "widget":
            done += setup_widget(dry_run, run)
        elif step == "bindings":
            done += setup_bindings(dry_run, run)
        elif step == "menu":
            done += setup_menu(dry_run)
        elif step == "launcher":
            done += setup_launcher(binary, dry_run)
    return done


def teardown(only=(), skip=(), dry_run=False, run=subprocess.run):
    done = []
    # Reverse order: take the front ends away before the daemon they talk to.
    for step in reversed(selected(only, skip)):
        if step == "service":
            done += teardown_service(dry_run, run)
        elif step == "hooks":
            done += teardown_hooks(dry_run)
        elif step == "widget":
            done += teardown_widget(dry_run, run)
        elif step == "bindings":
            done += teardown_bindings(dry_run, run)
        elif step == "menu":
            done += teardown_menu(dry_run)
        elif step == "launcher":
            done += teardown_launcher(dry_run)
    return done


def leftovers():
    """What teardown keeps, and how to remove it by hand."""
    from . import paths, worktrees
    return [
        f"kept your settings in {config.path().parent}",
        f"kept state (sessions, queue, spend) in {paths.state_dir()}",
        f"kept task worktrees in {worktrees.base_dir()}: review or merge them with `omaorchestra worktree list`",
        "kept provider keys in the keyring: `omaorchestra provider remove <id>` deletes one",
    ]
