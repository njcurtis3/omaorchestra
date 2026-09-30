import QtQuick
import QtQuick.Controls

// A filter or view switch: a label and an optional count, filled while
// `selected`; `urgent` colours it when something waits. Same height as
// AppButton, so a row of chips, a search field and buttons line up.
// `theme` comes from Python.
Button {
  id: chip
  property bool selected: false
  property bool urgent: false
  flat: true
  leftPadding: 12
  rightPadding: 12
  topPadding: 0
  bottomPadding: 0
  contentItem: Label {
    text: chip.text
    color: chip.selected ? theme.foreground : chip.urgent ? theme.urgent : theme.muted
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    elide: Text.ElideMiddle
  }
  background: Rectangle {
    radius: 4
    implicitHeight: 32
    color: chip.selected ? theme.selection : chip.hovered ? Qt.alpha(theme.selection, 0.5) : "transparent"
    border.color: theme.selection
  }
}
