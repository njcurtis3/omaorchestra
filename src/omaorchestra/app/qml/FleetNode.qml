import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One node of a run (present_fleet.node_detail): its role, agent, cost and
// time, what it was told (its brief), and what it handed back, read as
// lines. Its session opens in Sessions while it is live. `fleets` and
// `theme` come from Python.
Rectangle {
  id: panel
  property string runId: ""
  property string nodeId: ""
  signal openSession(string sessionId)
  signal openHistory(string sessionId)
  signal closed()
  property var d: ({})
  function reload() { d = runId && nodeId ? fleets.node(runId, nodeId) : ({}) }
  onNodeIdChanged: { reload(); showBrief = false }
  Connections { target: fleets; function onRunChanged(id) { if (id === panel.runId) panel.reload() } }
  property bool showBrief: false

  objectName: "node-detail"
  implicitHeight: body.implicitHeight + 24
  radius: 6
  color: theme.surface

  ColumnLayout {
    id: body
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
    spacing: 4

    RowLayout {
      Layout.fillWidth: true
      FleetDot { state_: panel.d.state || "queued" }
      Label {
        Layout.fillWidth: true
        text: (panel.d.id || "") + (panel.d.runsAs && panel.d.runsAs !== panel.d.role ? "  as " + panel.d.runsAs : "")
        color: theme.foreground
        font.bold: true
      }
      AppButton {
        objectName: "node-open-session"
        visible: !!panel.d.session
        text: panel.d.live ? "Open session" : "Open in History"
        onClicked: panel.d.live ? panel.openSession(panel.d.session) : panel.openHistory(panel.d.session)
      }
      IconButton { glyph: "󰅖"; tip: "Close"; onActivated: panel.closed() }
    }
    Label {
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.muted
      font.pixelSize: 12
      text: [panel.d.status, panel.d.agent, panel.d.length, panel.d.cost ? (panel.d.costReal ? "$" : "~$") + panel.d.cost.toFixed(2) : "",
             panel.d.branch ? "on " + panel.d.branch : ""].filter(Boolean).join("  ·  ")
    }
    Label { visible: !!panel.d.error; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere; color: theme.urgent; text: panel.d.error || "" }
    Label {
      visible: (panel.d.extra || []).length > 0
      Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere; color: panel.d.accepted ? theme.muted : theme.urgent
      text: "Outside its files: " + (panel.d.extra || []).join(", ") + (panel.d.accepted ? " (accepted: " + panel.d.accepted + ")" : "")
    }
    Label {
      visible: (panel.d.feedback || []).length > 0
      Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere; color: theme.accent
      text: "Your note: " + (panel.d.feedback || []).join("; ")
    }

    Label { visible: (panel.d.result || []).length > 0; text: "What it handed back"; color: theme.muted; font.pixelSize: 12; Layout.topMargin: 6 }
    Repeater {
      model: panel.d.result || []
      delegate: RowLayout {
        required property var modelData
        Layout.fillWidth: true
        spacing: 8
        Label { text: modelData.label; color: theme.muted; font.pixelSize: 12; Layout.fillWidth: true; Layout.preferredWidth: 90; Layout.maximumWidth: 90; elide: Text.ElideRight; Layout.alignment: Qt.AlignTop }
        Label {
          text: modelData.text
          Layout.fillWidth: true
          wrapMode: Text.WrapAtWordBoundaryOrAnywhere
          color: ["blocker", "risk", "plan-killer", "blocked", "escalate"].indexOf(modelData.label) >= 0 ? theme.urgent : theme.foreground
          font.family: modelData.label === "output" ? fontFamily : font.family
          font.pixelSize: modelData.label === "output" ? 12 : 13
          maximumLineCount: modelData.label === "output" ? 8 : 40
          elide: Text.ElideRight
        }
      }
    }

    AppButton {
      objectName: "node-brief"
      Layout.topMargin: 6
      text: panel.showBrief ? "Hide what it was told" : "What it was told"
      onClicked: panel.showBrief = !panel.showBrief
    }
    Label {
      visible: panel.showBrief
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      text: panel.d.brief || ""
      color: theme.foreground
      font.family: fontFamily
      font.pixelSize: 12
    }
  }
}
