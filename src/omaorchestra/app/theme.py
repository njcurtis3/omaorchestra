"""Read the current Omarchy theme's colours. No Qt here, so it is testable anywhere."""

import os
import re
import tomllib
from pathlib import Path

# Used for anything the theme does not define (and when there is no theme).
DEFAULTS = {
    "background": "#101315",
    "surface": "#1a1f22",
    "foreground": "#cacccc",
    "accent": "#6e9fb0",
    "muted": "#707880",
    "selection": "#2a3136",
    "urgent": "#a55555",
    "mode": "dark",
}

# The built-in light palette, for "light" in the app's settings when the
# Omarchy theme is dark: the same hues as DEFAULTS, darkened for contrast.
LIGHT = {
    "background": "#f4f5f6",
    "surface": "#e8ebed",
    "foreground": "#1e2326",
    "accent": "#3d7286",
    "muted": "#5f6870",
    "selection": "#d3d9dd",
    "urgent": "#b03a3a",
    "mode": "light",
}

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def theme_dir():
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "omarchy" / "current" / "theme"


def colors_path():
    return theme_dir() / "colors.toml"


def palette(colors):
    """Map an Omarchy colors.toml onto the few roles the app uses."""
    def pick(*names):
        for name in names:
            value = colors.get(name)
            if isinstance(value, str) and HEX.match(value.strip()):
                return value.strip()
        return None

    result = dict(DEFAULTS)
    for role, names in {
        "background": ("background",),
        "surface": ("lighter_background", "dark_background", "selection"),
        "foreground": ("foreground",),
        "accent": ("accent", "blue"),
        "muted": ("muted", "dark_foreground"),
        "selection": ("selection", "lighter_background"),
        "urgent": ("red", "bright_red"),
    }.items():
        result[role] = pick(*names) or DEFAULTS[role]
    if colors.get("mode") in ("dark", "light"):
        result["mode"] = colors["mode"]
    return result


def for_mode(colors, mode):
    """The palette to draw with for the app's appearance setting: the
    theme's own for "system" or when it is already that mode, otherwise
    the built-in one for the mode asked for."""
    if mode not in ("light", "dark") or colors["mode"] == mode:
        return colors
    return dict(LIGHT if mode == "light" else DEFAULTS)


def load(path=None):
    try:
        with open(path or colors_path(), "rb") as f:
            return palette(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError):
        return dict(DEFAULTS)
