// Run: node --test tests/plugin
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

// Format.js is a QML ".pragma library"; strip the pragma and load it as a script.
const src = readFileSync(new URL("../../plugin/omaorchestra.sessions/Format.js", import.meta.url), "utf8")
const F = new Function(src.replace(/^\.pragma library\s*$/m, "") +
  "\nreturn { projectName, ago, modelName, statusLabel, sessionList, sorted, counts, barLabel, tooltip, awayText }")()

test("projectName uses the last path component", () => {
  assert.equal(F.projectName("/home/u/Work/proj"), "proj")
  assert.equal(F.projectName("/home/u/Work/proj/"), "proj")
  assert.equal(F.projectName(""), "(unknown)")
  assert.equal(F.projectName("/"), "/")
})

test("ago picks a sensible unit", () => {
  assert.equal(F.ago(5), "5s ago")
  assert.equal(F.ago(125), "2m ago")
  assert.equal(F.ago(7300), "2h ago")
  assert.equal(F.ago(-3), "0s ago")
})

test("sessionList accepts the registry object and drops junk", () => {
  const list = F.sessionList({ a: { id: "a" }, b: null, c: { status: "idle" } })
  assert.deepEqual(list.map(s => s.id), ["a"])
  assert.deepEqual(F.sessionList(null), [])
})

test("sorted puts waiting first, then working, then idle, newest first", () => {
  const out = F.sorted([
    { id: "idle", status: "idle", updated: 9 },
    { id: "work-old", status: "working", updated: 1 },
    { id: "wait", status: "needs-input", updated: 0 },
    { id: "work-new", status: "working", updated: 5 },
  ])
  assert.deepEqual(out.map(s => s.id), ["wait", "work-new", "work-old", "idle"])
})

test("bar label and tooltip reflect counts", () => {
  const c = F.counts([{ status: "needs-input" }, { status: "working" }, { status: "working" }, { status: "idle" }])
  assert.deepEqual(c, { total: 4, waiting: 1, working: 2, idle: 1 })
  assert.equal(F.barLabel("G", c), "G 1 waiting")
  assert.equal(F.barLabel("G", F.counts([{ status: "working" }])), "G 1")
  assert.equal(F.barLabel("G", F.counts([{ status: "idle" }])), "G")
  assert.equal(F.tooltip(c), "omaorchestra: 1 waiting · 2 working · 1 idle")
  assert.equal(F.tooltip(F.counts([])), "omaorchestra: no agent sessions")
})

test("modelName matches the Python formatting", () => {
  assert.equal(F.modelName("claude-opus-5-5"), "Opus 5.5")
  assert.equal(F.modelName("claude-haiku-4-5-20251001"), "Haiku 4.5")
  assert.equal(F.modelName("claude-sonnet-5"), "Sonnet 5")
  assert.equal(F.modelName(""), "")
  assert.equal(F.modelName("gpt-5"), "Gpt 5")
})

test("away mode shows while pushes or remote answers are on", () => {
  const c = { total: 1, waiting: 1, working: 0, idle: 0 }
  const locked = { push: true, answers: true, active: true, away: true, reason: "locked", mode: "auto" }
  assert.equal(F.barLabel("G", c, locked), "G 1 waiting 󰄜")
  assert.equal(F.barLabel("G", c, { ...locked, push: false, answers: false, active: false }), "G 1 waiting")
  assert.equal(F.barLabel("G", c, null), "G 1 waiting")
  assert.equal(F.tooltip(c, locked), "omaorchestra: 1 waiting\nAway (locked): pushing to your phone")
  assert.equal(F.awayText({ ...locked, push: false }), "Away (locked): answering prompts from your phone")
  assert.equal(F.tooltip(c, { push: false, active: false }), "omaorchestra: 1 waiting")
  assert.equal(F.awayText({ active: true, away: false, mode: "auto" }), "At the desk")
  assert.equal(F.awayText({ active: true, away: false, mode: "off" }), "At the desk (until you switch)")
  assert.equal(F.awayText({ push: true, away: true, reason: "on", mode: "on" }), "Away: pushing to your phone")
})
