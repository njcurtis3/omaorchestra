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

  property string page: "sessions"

  // Called from Python when asked to open a session (`app --session <id>`).
  // A prefix is enough; it is matched against the current sessions.
  function showSession(id) {
    const match = sessions.rows.find(s => s.id.startsWith(id))
    window.page = "sessions"
    sessionsPage.selectedId = match ? match.id : id
  }
  Component.onCompleted: if (initialSession) showSession(initialSession)
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
          text: window.page === "sessions" ? "Sessions" : "Settings"
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

      // Settings
      SettingsPage {
        visible: window.page === "settings"
        Layout.fillWidth: true
        Layout.fillHeight: true
      }
    }
  }
}
