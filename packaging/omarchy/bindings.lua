-- omaorchestra keybindings for Omarchy. Copy into ~/.config/hypr/bindings.lua.
-- Both combinations are free in a stock Omarchy and sit next to its other
-- AI bindings (SUPER + SHIFT + CTRL + A opens the agent).

-- Jump to the agent that needs you most (waiting first, then working).
o.bind("SUPER + ALT + A", "Jump to waiting agent", "omaorchestra focus --notify")

-- Open or close the agent sessions panel on the focused monitor.
o.bind("SUPER + CTRL + ALT + A", "Agent sessions", "omarchy-shell shell toggle omaorchestra.sessions")

-- Open the omaorchestra app (a second press focuses the open window).
o.bind("SUPER + SHIFT + CTRL + ALT + A", "omaorchestra", "omaorchestra app")
