import QtQuick
import QtQuick.Controls

// The app's one button, 32 high like its text fields: outlined, `primary`
// (filled with the accent), `danger` (urgent outline), `quiet` (a link:
// accent text, no outline) or `active` (a toggle that is on). `theme` comes
// from Python.
Button {
  id: button
  property bool primary: false
  property bool danger: false
  property bool quiet: false
  property bool active: false
  flat: true
  leftPadding: quiet ? 8 : 14
  rightPadding: quiet ? 8 : 14
  topPadding: 0
  bottomPadding: 0
  contentItem: Label {
    text: button.text
    color: !button.enabled ? theme.muted
           : button.primary ? theme.background
           : button.danger ? theme.urgent
           : button.quiet || button.active ? theme.accent : theme.foreground
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    elide: Text.ElideRight
  }
  background: Rectangle {
    radius: 4
    implicitWidth: 64
    implicitHeight: 32
    color: button.primary && button.enabled ? theme.accent
           : button.hovered && button.enabled ? theme.selection : "transparent"
    border.color: button.quiet || button.primary && button.enabled ? "transparent"
                  : button.danger ? theme.urgent : button.active ? theme.accent : theme.selection
  }
}
