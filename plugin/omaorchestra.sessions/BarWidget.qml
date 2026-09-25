import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Format.js" as Format

// omaorchestra sessions for the bar, and the host for the detail panel.
//
// The widget never talks to the daemon: omaorchestrad rewrites sessions.json
// (atomically, via rename) on every change, and a FileView watch picks that
// up immediately. No polling process, and nothing to break if the daemon is
// down -- the bar just shows the last state it wrote.
BarWidget {
  id: root
  moduleName: "omaorchestra.sessions"

  readonly property string glyph: "󰚩"
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")
  readonly property string registryPath: stateHome + "/omaorchestra/sessions.json"
  readonly property string awayPath: stateHome + "/omaorchestra/away.json"

  property var sessions: []
  // Away mode, from away.json (the daemon rewrites it as you come and go).
  property var away: null
  // A FileView only watches a file that exists, so a registry created after
  // the bar loaded (first daemon start) is noticed by the retry timer below.
  property bool registryLoaded: false
  // Bumped every second while the panel is open so relative times tick.
  property real now: Date.now() / 1000

  readonly property var counts: Format.counts(sessions)

  function parse(content) {
    try {
      root.sessions = Format.sorted(Format.sessionList(JSON.parse(String(content || ""))))
    } catch (e) {
      // Keep the last good state; the next write corrects it.
    }
  }

  function refresh() {
    registry.reload()
    awayFile.reload()
    root.now = Date.now() / 1000
  }

  // ---- Panel lifecycle. Bar.findPanelWidget requires open/close/opened on
  //      the bar-widget root, so these forward to the loaded panel.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  readonly property real openPanelIndicatorWidth: button.labelWidth
  readonly property real openPanelIndicatorHeight: Math.max(Style.space(10), Math.round(Style.bar.iconSlot * 0.55))

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  FileView {
    id: registry
    path: root.registryPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      root.registryLoaded = true
      root.parse(text())
    }
    onLoadFailed: {
      root.registryLoaded = false
      root.sessions = []
    }
  }

  FileView {
    id: awayFile
    path: root.awayPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.away = JSON.parse(String(text() || "")) } catch (e) {}
    }
    onLoadFailed: root.away = null
  }

  Timer {
    interval: 5000
    running: !root.registryLoaded || root.away === null
    repeat: true
    onTriggered: {
      if (!root.registryLoaded) registry.reload()
      if (root.away === null) awayFile.reload()
    }
  }

  Timer {
    interval: 1000
    running: root.opened
    repeat: true
    triggeredOnStart: true
    onTriggered: root.now = Date.now() / 1000
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "omaorchestra.sessions"

    function status(): string {
      return JSON.stringify({ path: root.registryPath, loaded: root.registryLoaded, counts: root.counts, sessions: root.sessions.length, away: root.away })
    }
    function refresh(): void { root.refresh() }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.vertical ? root.glyph : Format.barLabel(root.glyph, root.counts, root.away)
    // Urgent colour only when an agent is blocked on you.
    active: root.counts.waiting > 0
    dimmed: root.counts.waiting === 0 && root.counts.working === 0
    tooltipText: Format.tooltip(root.counts, root.away)

    onPressed: function(b) {
      if (b === Qt.RightButton) root.refresh()
      else root.togglePanel()
    }
  }
}
