-- omaorchestra window rule for Omarchy. Copy into ~/.config/hypr/hyprland.lua
-- (after require("default.hypr.omarchy")). The app's Wayland app id, and so
-- its Hyprland class, is "omaorchestra".
o.window("^omaorchestra$", { float = true, center = true, size = { 1100, 720 } })
