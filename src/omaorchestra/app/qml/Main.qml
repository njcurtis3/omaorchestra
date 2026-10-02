import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The app shell: navigation on the left, a page on the right, and the daemon
// connection in the header. `theme`, `sessions`, `fleets`, `settings`,
// `fontFamily`, `appVersion`, `initialSession` and `initialFleet` come from
// Python (app/main.py).
ApplicationWindow {
  id: window
  title: "omaorchestra"
  width: 1100
  height: 720
  minimumWidth: 640
  minimumHeight: 420
  visible: true
  color: theme.background

  font.family: fontFamily
  font.pixelSize: 14

  // Theme colours for every control that reads the palette (popups, menus,
  // text fields); controls that draw themselves take theme.* directly.
  palette.window: theme.surface
  palette.windowText: theme.foreground
  palette.base: theme.surface
  palette.text: theme.foreground
  palette.button: theme.surface
  palette.buttonText: theme.foreground
  palette.highlight: theme.selection
  palette.highlightedText: theme.foreground
  palette.placeholderText: theme.muted
  palette.mid: theme.selection
  palette.dark: theme.muted

  property string page: "sessions"
  // A narrow window: the navigation shows glyphs only.
  readonly property bool compact: width < 880
  // The page's width, from the window's: what a page switches its layout
  // on. A page's own width will not do, since a wide layout can hold it wide.
  readonly property real pageWidth: width - (compact ? 64 + 2 * 16 : 200 + 2 * 24)

  // Called from Python when asked to open a session (`app --session <id>`).
  // A prefix is enough; it is matched against the current sessions.
  function showSession(id) {
    const match = sessions.rows.find(s => s.id.startsWith(id))
    window.page = "sessions"
    sessionsPage.selectedId = match ? match.id : id
  }
  // `app --fleet <id>`, a notification or the bar: open omafleet on a run.
  function showFleet(id) {
    window.page = "fleets"
    fleetsPage.show(id)
  }
  function showHistory(sessionId) {
    window.page = "history"
    historyPage.selectedKey = sessionHistory.keyFor(sessionId)
  }
  Component.onCompleted: {
    if (initialSession) showSession(initialSession)
    if (initialFleet) showFleet(initialFleet)
  }
  Shortcut { sequence: "Ctrl+N"; onActivated: window.page = "new" }
  // A new page fades in rather than appearing at once.
  onPageChanged: pageFade.restart()
  NumberAnimation {
    id: pageFade
    targets: [sessionsPage, newTaskScroll, fleetsPage, queuePage, historyPage, worktreesPage,
              providersPage, usagePage, mcpPage, permissionsPage, settingsPage]
    property: "opacity"
    from: 0
    to: 1
    duration: 140
    easing.type: Easing.OutCubic
  }
  Connections {
    // At startup the session list has not arrived yet, so a prefix cannot be
    // matched; match it once the first snapshot lands.
    target: sessions
    enabled: !!initialSession
    function onChanged() {
      if (!sessions.connected) return
      window.showSession(initialSession)
      enabled = false
    }
  }
  // Ticks every second so relative times stay current.
  property real now: Date.now() / 1000
  Timer { interval: 1000; running: true; repeat: true; onTriggered: window.now = Date.now() / 1000 }

  readonly property var pages: [
    { id: "sessions", glyph: "󰚩", label: "Sessions" },
    { id: "new", glyph: "󰐕", label: "New task" },
    { id: "fleets", glyph: "󰡉", label: "omafleet" },
    { id: "queue", glyph: "󰒲", label: "Queue" },
    { id: "history", glyph: "󰋚", label: "History" },
    { id: "worktrees", glyph: "󰙅", label: "Worktrees" },
    { id: "providers", glyph: "󰒍", label: "Providers" },
    { id: "usage", glyph: "󰄨", label: "Usage" },
    { id: "mcp", glyph: "󰒓", label: "MCP" },
    { id: "permissions", glyph: "󰌾", label: "Permissions" },
    { id: "settings", glyph: "󰒓", label: "Settings" }
  ]

  RowLayout {
    anchors.fill: parent
    spacing: 0

    // ---------------------------------------------------------- Navigation
    // Glyphs only in a narrow window, so the page keeps the room; the
    // entries scroll when the window is short.
    Rectangle {
      objectName: "nav"
      Layout.fillHeight: true
      Layout.preferredWidth: window.compact ? 64 : 200
      color: theme.surface

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: window.compact ? 8 : 16
        spacing: 4

        // The wordmark, in the theme's mode (PNG: Qt reads SVG only with
        // qt6-svg, which pyside6 does not pull in); just its mark when narrow.
        Item {
          Layout.fillWidth: !window.compact
          Layout.preferredWidth: window.compact ? Math.ceil(logo.width * 81 / 554) : -1  // the mark ends 81 of 554 in
          Layout.alignment: Qt.AlignHCenter
          Layout.preferredHeight: logo.height
          Layout.bottomMargin: 16
          clip: true
          Image {
            id: logo
            objectName: "logo"
            source: theme.dark ? "logo-dark.png" : "logo-light.png"
            width: window.compact ? 168 : parent.width
            height: width * 72 / 454
            fillMode: Image.PreserveAspectFit
            horizontalAlignment: Image.AlignLeft
            mipmap: true
            Accessible.name: "omaorchestra"
          }
        }

        Flickable {
          id: navList
          Layout.fillWidth: true
          Layout.fillHeight: true
          contentHeight: navColumn.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          ScrollBar.vertical: ScrollBar { policy: navList.contentHeight > navList.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }

          ColumnLayout {
            id: navColumn
            width: navList.width
            spacing: 4

            Repeater {
              model: window.pages

              delegate: ItemDelegate {
                id: navItem
                required property var modelData
                objectName: "nav-" + modelData.id
                Layout.fillWidth: true
                highlighted: window.page === modelData.id
                implicitHeight: 38  // all eleven fit a 600-high window
                onClicked: window.page = modelData.id
                ToolTip.visible: window.compact && hovered
                ToolTip.delay: 300
                ToolTip.text: modelData.label

                contentItem: RowLayout {
                  spacing: 0
                  // The glyph takes the accent on the page you are on.
                  Label {
                    Layout.fillWidth: window.compact
                    horizontalAlignment: window.compact ? Text.AlignHCenter : Text.AlignLeft
                    text: navItem.modelData.glyph
                    color: navItem.highlighted ? theme.accent : navItem.hovered ? theme.foreground : theme.muted
                    Behavior on color { ColorAnimation { duration: 120 } }
                  }
                  Label {
                    visible: !window.compact
                    Layout.fillWidth: true
                    leftPadding: 10
                    text: navItem.modelData.label
                    color: navItem.highlighted || navItem.hovered ? theme.foreground : theme.muted
                    elide: Text.ElideRight
                    Behavior on color { ColorAnimation { duration: 120 } }
                  }
                  // Runs that need you: at a gate, held, or a node waiting.
                  Label {
                    objectName: "nav-badge-" + navItem.modelData.id
                    visible: !window.compact && navItem.modelData.id === "fleets" && fleets.needing > 0
                    text: String(fleets.needing)
                    color: theme.background
                    font.pixelSize: 11
                    font.bold: true
                    leftPadding: 6; rightPadding: 6
                    background: Rectangle { radius: 8; color: theme.urgent }
                  }
                }
                // The badge as a dot on the glyph when narrow.
                Rectangle {
                  visible: window.compact && navItem.modelData.id === "fleets" && fleets.needing > 0
                  width: 8; height: 8; radius: 4
                  color: theme.urgent
                  x: parent.width - 14; y: 6
                }
                background: Rectangle {
                  radius: 4
                  color: navItem.highlighted ? theme.selection : navItem.hovered ? Qt.alpha(theme.selection, 0.5) : Qt.alpha(theme.selection, 0)
                  Behavior on color { ColorAnimation { duration: 120 } }
                  Stripe { shown: navItem.highlighted; color: theme.accent }
                }
              }
            }
          }
        }

        // Away mode (`omaorchestra away`): pushes and remote answers happen only
        // while away. One row: where you are and the mode, the choices in its
        // list; just the glyph when the navigation is narrow.
        ComboBox {
          id: awayBox
          objectName: "away"
          visible: awayMode.known && awayMode.active && sessions.connected
          Layout.fillWidth: true
          Layout.topMargin: 8
          implicitHeight: 32
          font.pixelSize: 12
          model: [{ value: "auto", text: "Auto" },
                  { value: "on", text: "Away" },
                  { value: "off", text: "At the desk" }]
          textRole: "text"
          valueRole: "value"
          currentIndex: indexOfValue(awayMode.mode)
          displayText: window.compact ? "󰄜" : "󰄜  " + (awayMode.away ? "Away" : "At the desk")
          indicator.visible: !window.compact
          contentItem: Label {
            leftPadding: window.compact ? 0 : 10
            text: awayBox.displayText
            color: awayMode.away ? theme.accent : theme.muted
            font: awayBox.font
            horizontalAlignment: window.compact ? Text.AlignHCenter : Text.AlignLeft
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
          }
          background: Rectangle { radius: 4; color: awayBox.hovered ? Qt.alpha(theme.selection, 0.5) : "transparent"; border.color: theme.selection }
          popup.width: Math.max(awayBox.width, 160)
          // Choosing sets the mode; the daemon's answer (or a change made
          // elsewhere) sets what is shown.
          onActivated: awayMode.setMode(currentValue)
          Connections {
            target: awayMode
            function onChanged() { awayBox.currentIndex = awayBox.indexOfValue(awayMode.mode) }
          }
          ToolTip.visible: hovered && !popup.visible
          ToolTip.delay: 500
          ToolTip.text: awayMode.text + ". Mode: " + awayBox.currentText
                        + ".\n\nAuto is away once the screen locks or after a while without input. "
                        + "Pushes go out, and permission prompts can be answered remotely, only while you are away."
          palette.button: theme.background
          palette.buttonText: theme.foreground
          palette.base: theme.surface
          palette.text: theme.foreground
          palette.window: theme.surface
          palette.windowText: theme.foreground
          palette.highlight: theme.selection
          palette.highlightedText: theme.foreground
          palette.mid: theme.selection
          palette.dark: theme.muted
        }

        Label {
          Layout.fillWidth: true
          horizontalAlignment: window.compact ? Text.AlignHCenter : Text.AlignLeft
          text: (window.compact ? "" : "v") + appVersion
          color: theme.muted
          font.pixelSize: 12
        }
      }
    }

    // ---------------------------------------------------------- Page
    ColumnLayout {
      Layout.fillWidth: true
      Layout.fillHeight: true
      Layout.margins: window.compact ? 16 : 24
      spacing: 16
      clip: true  // nothing a page overflows draws over the navigation

      RowLayout {
        Layout.fillWidth: true

        Label {
          text: window.pages.find(p => p.id === window.page).label
          color: theme.foreground
          font.pixelSize: 22
          font.bold: true
          Layout.fillWidth: true
          elide: Text.ElideRight
        }

        Rectangle {
          width: 8; height: 8; radius: 4
          color: sessions.connected ? theme.accent : theme.urgent
        }
        Label {
          text: sessions.connected ? "Connected" : "Daemon not running"
          color: theme.muted
        }
      }

      Item {
        Layout.fillWidth: true
        implicitHeight: 2
        Rectangle {
          anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
          height: 1
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: theme.selection }
            GradientStop { position: 1; color: Qt.alpha(theme.selection, 0) }
          }
        }
        Rectangle { width: 32; height: 2; radius: 1; color: theme.accent }
      }

      // Sessions
      Label {
        visible: (window.page === "sessions" || window.page === "fleets") && !sessions.connected
        Layout.fillWidth: true
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        color: theme.muted
        text: "omaorchestrad is not running. Start it with `omaorchestra service install`; this window reconnects on its own."
      }

      SessionsPage {
        id: sessionsPage
        onOpenFleet: id => window.showFleet(id)
        visible: window.page === "sessions" && sessions.connected
        Layout.fillWidth: true
        Layout.fillHeight: true
        now: window.now
      }

      // New task
      // Scrolls when the window is shorter than the form (Then… and a
      // recipe make it taller).
      ScrollView {
        id: newTaskScroll
        visible: window.page === "new"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff  // never wider than its view; a hidden bar still takes clicks along the bottom

        NewTaskPage {
          width: newTaskScroll.availableWidth
          onLaunched: id => window.showSession(id)
          onQueued: window.page = "queue"
          onAsFleet: (goal, folder) => { window.page = "fleets"; fleetsPage.newRun(goal, folder) }
        }
      }

      // omafleet
      FleetsPage {
        id: fleetsPage
        visible: window.page === "fleets" && sessions.connected
        Layout.fillWidth: true
        Layout.fillHeight: true
        now: window.now
        onOpenSession: id => window.showSession(id)
        onOpenHistory: id => window.showHistory(id)
      }

      // Queue
      QueuePage {
        id: queuePage
        onOpenFleet: id => window.showFleet(id)
        visible: window.page === "queue"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // History
      HistoryPage {
        id: historyPage
        onOpenFleet: id => window.showFleet(id)
        visible: window.page === "history"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Worktrees
      WorktreesPage {
        id: worktreesPage
        onOpenFleet: id => window.showFleet(id)
        visible: window.page === "worktrees"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Providers
      ProvidersPage {
        id: providersPage
        visible: window.page === "providers"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Usage
      UsagePage {
        id: usagePage
        visible: window.page === "usage"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // MCP
      McpPage {
        id: mcpPage
        visible: window.page === "mcp"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Permissions
      PermissionsPage {
        id: permissionsPage
        visible: window.page === "permissions"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Settings
      SettingsPage {
        id: settingsPage
        visible: window.page === "settings"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }
    }
  }
}
