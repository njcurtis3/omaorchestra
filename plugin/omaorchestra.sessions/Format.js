.pragma library

// Pure helpers shared by the bar label and the panel, so the two never
// disagree. Kept free of QML types so they can be unit-tested with node.

var ORDER = { "needs-input": 0, "working": 1, "idle": 2 }

function projectName(cwd) {
  var raw = String(cwd || "")
  if (!raw) return "(unknown)"
  var path = raw.replace(/\/+$/, "")
  if (!path) return "/"
  return path.slice(path.lastIndexOf("/") + 1)
}

function ago(seconds) {
  var s = Math.max(0, Math.floor(Number(seconds)))
  if (!isFinite(s)) return ""
  if (s < 60) return s + "s ago"
  if (s < 3600) return Math.floor(s / 60) + "m ago"
  if (s < 86400) return Math.floor(s / 3600) + "h ago"
  return Math.floor(s / 86400) + "d ago"
}

// "claude-opus-5-5" -> "Opus 5.5"; mirrors transcript.model_name in Python.
function modelName(model) {
  if (!model) return ""
  var name = String(model).replace(/^claude-/, "").replace(/-\d{8}$/, "")
  var parts = name.split("-")
  var words = parts.filter(function(p) { return !/^\d+$/.test(p) })
  var version = parts.filter(function(p) { return /^\d+$/.test(p) }).join(".")
  if (!words.length) return String(model)
  return words.map(function(w) { return w.charAt(0).toUpperCase() + w.slice(1) }).join(" ") + (version ? " " + version : "")
}

function statusLabel(status) {
  if (status === "needs-input") return "waiting for you"
  if (status === "working") return "working"
  if (status === "idle") return "idle"
  return String(status || "unknown")
}

// sessions.json is an object keyed by session id; accept an array too.
function sessionList(registry) {
  if (!registry || typeof registry !== "object") return []
  var list = Array.isArray(registry) ? registry.slice() : Object.keys(registry).map(function(k) { return registry[k] })
  return list.filter(function(s) { return s && typeof s === "object" && s.id })
}

// Waiting first (it needs you), then working, then idle; newest first within each.
function sorted(sessions) {
  return sessions.slice().sort(function(a, b) {
    var ra = a.status in ORDER ? ORDER[a.status] : 3
    var rb = b.status in ORDER ? ORDER[b.status] : 3
    if (ra !== rb) return ra - rb
    return (Number(b.updated) || 0) - (Number(a.updated) || 0)
  })
}

function counts(sessions) {
  var c = { total: sessions.length, waiting: 0, working: 0, idle: 0 }
  sessions.forEach(function(s) {
    if (s.status === "needs-input") c.waiting++
    else if (s.status === "working") c.working++
    else if (s.status === "idle") c.idle++
  })
  return c
}

var PHONE = "󰄜"

// Whether away mode changes anything: pushes or remote answers are on
// (away.json, written by the daemon; older daemons only wrote push).
function awayActive(away) {
  return !!(away && (away.active !== undefined ? away.active : away.push))
}

// Away, while that changes anything.
function awayNow(away) {
  return awayActive(away) && !!away.away
}

function barLabel(glyph, c, away) {
  var label = glyph
  if (c.waiting > 0) label = glyph + " " + c.waiting + " waiting"
  else if (c.working > 0) label = glyph + " " + c.working
  return awayNow(away) ? label + " " + PHONE : label
}

// One line on away mode, or "" while it changes nothing.
function awayText(away) {
  if (!awayActive(away)) return ""
  if (away.away) {
    var why = away.reason === "locked" ? " (locked)" : away.reason === "idle" ? " (idle)" : ""
    return "Away" + why + (away.push ? ": pushing to your phone" : ": answering prompts from your phone")
  }
  return away.mode === "off" ? "At the desk (until you switch)" : "At the desk"
}

function tooltip(c, away) {
  var line = awayText(away)
  return sessionsTooltip(c) + (line ? "\n" + line : "")
}

function sessionsTooltip(c) {
  if (c.total === 0) return "omaorchestra: no agent sessions"
  var parts = []
  if (c.waiting) parts.push(c.waiting + " waiting")
  if (c.working) parts.push(c.working + " working")
  if (c.idle) parts.push(c.idle + " idle")
  return "omaorchestra: " + parts.join(" · ")
}
