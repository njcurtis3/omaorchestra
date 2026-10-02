import QtQuick

// A session's state as a dot: working breathes, waiting sends out a soft
// ring, idle sits still. `theme` comes from Python.
Item {
  id: dot
  property string status: "idle"
  readonly property color tint: status === "needs-input" ? theme.urgent : status === "working" ? theme.accent : theme.muted
  implicitWidth: 10
  implicitHeight: 10

  Rectangle {
    id: ring
    anchors.centerIn: parent
    width: 10; height: 10; radius: width / 2
    color: "transparent"
    border.color: dot.tint
    border.width: 1.5
    opacity: 0
    visible: dot.status === "needs-input"
    ParallelAnimation {
      running: ring.visible
      loops: Animation.Infinite
      NumberAnimation { target: ring; property: "width"; from: 10; to: 22; duration: 1600; easing.type: Easing.OutCubic }
      NumberAnimation { target: ring; property: "height"; from: 10; to: 22; duration: 1600; easing.type: Easing.OutCubic }
      NumberAnimation { target: ring; property: "opacity"; from: 0.6; to: 0; duration: 1600; easing.type: Easing.OutCubic }
    }
  }

  Rectangle {
    anchors.fill: parent
    radius: width / 2
    color: dot.tint
    Behavior on color { ColorAnimation { duration: 200 } }
    SequentialAnimation on opacity {
      running: dot.status === "working"
      loops: Animation.Infinite
      alwaysRunToEnd: true  // back to full once it stops
      NumberAnimation { to: 0.4; duration: 900; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1; duration: 900; easing.type: Easing.InOutSine }
    }
  }
}
