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
    "muted": "#82898f",  # readable on background and surface (see readable_muted)
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


def _rgb(value):
    h = value.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h[:6]
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _luminance(value):
    c = [x / 255 for x in _rgb(value)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    """WCAG contrast ratio between two colours, 1 to 21."""
    a, b = _luminance(a), _luminance(b)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def readable_muted(colors, target=4.5, keep=1.8):
    """The theme's muted colour, moved toward the foreground until secondary
    text reads on the background and on rows (surface). Themes make muted
    for code comments, often under 2:1, which is too faint for paths and
    labels. It stops short of `keep` from the foreground, so secondary text
    still looks secondary in a low-contrast theme."""
    muted, step = colors["muted"], 0
    while step < 20 and min(contrast(muted, colors["background"]), contrast(muted, colors["surface"])) < target:
        nearer = _mix(colors["muted"], colors["foreground"], (step + 1) / 20)
        if contrast(nearer, colors["foreground"]) < keep:
            break
        muted, step = nearer, step + 1
    return muted


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
    result["muted"] = readable_muted(result)
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
