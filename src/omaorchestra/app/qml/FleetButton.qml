import QtQuick
import QtQuick.Controls

// A button in the app's two styles: flat (outlined), or `primary` (filled
// with the accent). `theme` comes from Python.
Button {
  id: button
  property bool primary: false
  property bool danger: false
  flat: true
  contentItem: Label {
    text: button.text
    color: !button.enabled ? theme.muted : button.primary ? theme.background : button.danger ? theme.urgent : theme.foreground
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
  }
  background: Rectangle {
    radius: 4
    implicitWidth: Math.max(90, button.contentItem.implicitWidth + 24)
    implicitHeight: 32
    color: button.primary && button.enabled ? theme.accent
           : button.hovered && button.enabled ? theme.selection : "transparent"
    border.color: button.danger ? theme.urgent : theme.selection
  }
}
