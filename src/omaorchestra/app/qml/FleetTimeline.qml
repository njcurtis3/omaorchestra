import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A run over time (present_fleet.timeline): a lane per node, working in the
// accent, waiting for you in urgent, and the stretches the run waited at a
// gate. Shows what really ran in parallel and where the time went.
// `fleets` and `theme` come from Python.
ColumnLayout {
  id: timeline
  property string runId: ""
  property var data_: ({ lanes: [], gates: [], length: "" })
  function reload() { data_ = runId ? fleets.timeline(runId) : ({ lanes: [], gates: [], length: "" }) }
  onRunIdChanged: reload()
  onVisibleChanged: if (visible) reload()
  Connections { target: fleets; function onRunChanged(id) { if (id === timeline.runId && timeline.visible) timeline.reload() } }
  spacing: 4

  Label {
    text: "Over " + (timeline.data_.length || "no time yet") + ".  Working ▬  waiting for you ▬  at a gate ░"
    color: theme.muted
    font.pixelSize: 12
  }

  Repeater {
    model: timeline.data_.lanes
    delegate: RowLayout {
      required property var modelData
      Layout.fillWidth: true
      spacing: 8
      Label { text: modelData.node; color: theme.foreground; font.pixelSize: 12; Layout.preferredWidth: 110; elide: Text.ElideRight }
      Item {
        id: lane
        Layout.fillWidth: true
        implicitHeight: 14
        Rectangle { anchors.fill: parent; radius: 3; color: theme.surface }
        Repeater {
          model: timeline.data_.gates
          delegate: Rectangle {
            required property var modelData
            x: modelData.x * lane.width; width: Math.max(2, modelData.w * lane.width); height: lane.height
            color: Qt.alpha(theme.muted, 0.25)
          }
        }
        Repeater {
          model: modelData.segments
          delegate: Rectangle {
            required property var modelData
            x: modelData.x * lane.width; width: Math.max(2, modelData.w * lane.width); height: lane.height
            radius: 3
            color: modelData.kind === "waiting" ? theme.urgent : theme.accent
            ToolTip.visible: segMouse.containsMouse
            ToolTip.text: (modelData.kind === "waiting" ? "waited for you " : "worked ") + modelData.minutes + " min"
            MouseArea { id: segMouse; anchors.fill: parent; hoverEnabled: true }
          }
        }
      }
      Label {
        text: modelData.waited ? "waited " + modelData.waited + "m" : ""
        color: theme.urgent
        font.pixelSize: 12
        Layout.preferredWidth: 80
      }
    }
  }
}
