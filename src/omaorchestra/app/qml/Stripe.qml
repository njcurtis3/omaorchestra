import QtQuick

// A thin bar inside a row's left edge: what marks the row as needing you
// (or selected) without outlining all of it. Fades in and out with `shown`.
Rectangle {
  property bool shown: true
  anchors { left: parent.left; top: parent.top; bottom: parent.bottom; topMargin: 8; bottomMargin: 8 }
  width: 3
  radius: 1.5
  opacity: shown ? 1 : 0
  visible: opacity > 0
  Behavior on opacity { NumberAnimation { duration: 150 } }
}
