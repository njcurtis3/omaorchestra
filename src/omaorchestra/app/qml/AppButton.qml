import QtQuick
import QtQuick.Controls

// The app's one button, 32 high like its text fields: outlined, `primary`
// (filled with the accent), `danger` (urgent outline), `quiet` (a link:
// accent text, no outline) or `active` (a toggle that is on). Lightens under
// the pointer and darkens while pressed. `theme` comes from Python.
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
    color: button.primary && button.enabled
             ? (button.pressed ? Qt.darker(theme.accent, 1.15) : button.hovered ? Qt.lighter(theme.accent, 1.12) : theme.accent)
           : button.pressed && button.enabled ? Qt.darker(theme.selection, 1.1)
           : button.hovered && button.enabled ? (button.danger ? Qt.alpha(theme.urgent, 0.12) : theme.selection)
           : Qt.alpha(theme.selection, 0)
    border.color: button.quiet || button.primary && button.enabled ? "transparent"
                  : button.danger ? theme.urgent : button.active ? theme.accent : theme.selection
    Behavior on color { ColorAnimation { duration: 120 } }
    FocusRing { control: button }
  }
}
