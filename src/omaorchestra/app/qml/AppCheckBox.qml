import QtQuick
import QtQuick.Controls

// A checkbox in theme colours: an accent box with a check when on, a muted
// outline when off, dimmed while disabled. `theme` comes from Python.
CheckBox {
  id: box
  contentItem: Label {
    text: box.text
    color: box.enabled ? theme.foreground : theme.muted
    leftPadding: box.indicator.width + 6
    verticalAlignment: Text.AlignVCenter
  }
  indicator: Rectangle {
    implicitWidth: 18
    implicitHeight: 18
    x: box.leftPadding
    y: (box.height - height) / 2
    radius: 3
    color: box.checked ? theme.accent : Qt.alpha(theme.accent, 0)
    border.color: box.checked ? theme.accent : box.hovered ? theme.foreground : theme.muted
    opacity: box.enabled ? 1 : 0.5
    Behavior on color { ColorAnimation { duration: 120 } }
    Label {
      anchors.centerIn: parent
      visible: box.checked
      text: "󰄬"
      color: theme.background
      font.pixelSize: 14
    }
    FocusRing { control: box; gap: 2 }
  }
}
