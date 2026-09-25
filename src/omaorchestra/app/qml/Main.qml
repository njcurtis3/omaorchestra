import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The app shell: navigation on the left, a page on the right, and the daemon
// connection in the header. `theme`, `sessions`, `settings`, `fontFamily`,
// `appVersion` and `initialSession` come from Python (app/main.py).
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

  // Called from Python when asked to open a session (`app --session <id>`).
  // A prefix is enough; it is matched against the current sessions.
  function showSession(id) {
    const match = sessions.rows.find(s => s.id.startsWith(id))
    window.page = "sessions"
    sessionsPage.selectedId = match ? match.id : id
  }
  Component.onCompleted: if (initialSession) showSession(initialSession)
  Shortcut { sequence: "Ctrl+N"; onActivated: window.page = "new" }
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
    Rectangle {
      Layout.fillHeight: true
      Layout.preferredWidth: 200
      color: theme.surface

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 4

        Label {
          text: "omaorchestra"
          color: theme.foreground
          font.pixelSize: 18
          font.bold: true
          Layout.bottomMargin: 16
        }

        Repeater {
          model: window.pages

          delegate: ItemDelegate {
            required property var modelData
            objectName: "nav-" + modelData.id
            Layout.fillWidth: true
            highlighted: window.page === modelData.id
            onClicked: window.page = modelData.id

            contentItem: Label {
              text: modelData.glyph + "  " + modelData.label
              color: parent.highlighted ? theme.foreground : theme.muted
            }
            background: Rectangle {
              radius: 4
              color: parent.highlighted ? theme.selection : parent.hovered ? Qt.alpha(theme.selection, 0.5) : "transparent"
            }
          }
        }

        Item { Layout.fillHeight: true }

        // Away mode (`omaorchestra away`): pushes and remote answers happen only while away.
        ColumnLayout {
          objectName: "away"
          visible: awayMode.known && awayMode.active && sessions.connected
          Layout.fillWidth: true
          Layout.bottomMargin: 12
          spacing: 6

          Label {
            text: "Away mode"
            color: theme.muted
            font.pixelSize: 12
          }

          ComboBox {
            id: awayBox
            objectName: "away-mode"
            Layout.fillWidth: true
            font.pixelSize: 12
            model: [{ value: "auto", text: "Auto" },
                    { value: "on", text: "Away" },
                    { value: "off", text: "At the desk" }]
            textRole: "text"
            valueRole: "value"
            currentIndex: indexOfValue(awayMode.mode)
            // Choosing sets the mode; the daemon's answer (or a change made
            // elsewhere) sets what is shown.
            onActivated: awayMode.setMode(currentValue)
            Connections {
              target: awayMode
              function onChanged() { awayBox.currentIndex = awayBox.indexOfValue(awayMode.mode) }
            }
            ToolTip.visible: hovered
            ToolTip.delay: 500
            ToolTip.text: "Auto: away once the screen locks or after a while without input. Pushes go out, "
                          + "and permission prompts can be answered remotely, only while you are away."
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
            text: "󰄜  " + awayMode.text
            color: awayMode.away ? theme.foreground : theme.muted
            font.pixelSize: 12
            wrapMode: Text.Wrap
            Layout.fillWidth: true
          }
        }

        Label {
          text: "v" + appVersion
          color: theme.muted
          font.pixelSize: 12
        }
      }
    }

    // ---------------------------------------------------------- Page
    ColumnLayout {
      Layout.fillWidth: true
      Layout.fillHeight: true
      Layout.margins: 24
      spacing: 16

      RowLayout {
        Layout.fillWidth: true

        Label {
          text: window.pages.find(p => p.id === window.page).label
          color: theme.foreground
          font.pixelSize: 22
          font.bold: true
          Layout.fillWidth: true
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

      Rectangle { Layout.fillWidth: true; height: 1; color: theme.selection }

      // Sessions
      Label {
        visible: window.page === "sessions" && !sessions.connected
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: theme.muted
        text: "omaorchestrad is not running. Start it with `omaorchestra service install`; this window reconnects on its own."
      }

      SessionsPage {
        id: sessionsPage
        visible: window.page === "sessions" && sessions.connected
        Layout.fillWidth: true
        Layout.fillHeight: true
        now: window.now
      }

      // New task
      NewTaskPage {
        visible: window.page === "new"
        Layout.fillWidth: true
        Layout.fillHeight: true
        onLaunched: id => window.showSession(id)
        onQueued: window.page = "queue"
      }

      // Queue
      QueuePage {
        visible: window.page === "queue"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // History
      HistoryPage {
        visible: window.page === "history"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Worktrees
      WorktreesPage {
        visible: window.page === "worktrees"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Providers
      ProvidersPage {
        visible: window.page === "providers"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Usage
      UsagePage {
        visible: window.page === "usage"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // MCP
      McpPage {
        visible: window.page === "mcp"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Permissions
      PermissionsPage {
        visible: window.page === "permissions"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }

      // Settings
      SettingsPage {
        visible: window.page === "settings"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }
    }
  }
}
