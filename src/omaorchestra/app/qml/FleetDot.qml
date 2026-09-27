import QtQuick

// A node's state as a small dot: queued (hollow), running (accent, pulsing),
// you (waiting for you), stalled, passed, rejected, failed, held, skipped.
Rectangle {
  id: dot
  property string state_: "queued"
  width: 10; height: 10; radius: 5
  readonly property bool bad: ["rejected", "failed", "held", "you", "stalled"].indexOf(state_) >= 0
  color: state_ === "queued" || state_ === "skipped" ? "transparent"
         : bad ? theme.urgent : state_ === "passed" ? theme.foreground : theme.accent
  border.color: state_ === "queued" || state_ === "skipped" ? theme.muted : color
  border.width: 1
  opacity: state_ === "skipped" ? 0.4 : 1
  SequentialAnimation on opacity {
    running: dot.state_ === "running" || dot.state_ === "you"
    loops: Animation.Infinite
    NumberAnimation { to: 0.35; duration: 700 }
    NumberAnimation { to: 1; duration: 700 }
  }
}
