import QtQuick
import QtQuick.Controls

// A filter or view switch: a label and an optional `count` in a small pill,
// filled while `selected`; `urgent` colours it when something waits. Same
// height as AppButton, so a row of chips, a search field and buttons line up.
// `theme` comes from Python.
Button {
  id: chip
  property bool selected: false
  property bool urgent: false
  property string count: ""
  flat: true
  leftPadding: 12
  rightPadding: count !== "" ? 6 : 12
  topPadding: 0
  bottomPadding: 0
  contentItem: Row {
    spacing: 8
    Label {
      anchors.verticalCenter: parent.verticalCenter
      text: chip.text
      color: chip.selected ? theme.foreground : chip.urgent ? theme.urgent : theme.muted
      elide: Text.ElideMiddle
      Behavior on color { ColorAnimation { duration: 120 } }
    }
    Label {
      objectName: "count"
      anchors.verticalCenter: parent.verticalCenter
      visible: chip.count !== ""
      text: chip.count
      color: chip.urgent ? theme.background : chip.selected ? theme.foreground : theme.muted
      font.pixelSize: 11
      font.bold: chip.urgent
      leftPadding: 7; rightPadding: 7; topPadding: 2; bottomPadding: 2
      background: Rectangle {
        radius: height / 2
        color: chip.urgent ? theme.urgent : Qt.alpha(theme.foreground, chip.selected ? 0.12 : 0.07)
      }
    }
  }
  background: Rectangle {
    radius: 4
    implicitHeight: 32
    color: chip.selected ? theme.selection : chip.hovered ? Qt.alpha(theme.selection, 0.5) : Qt.alpha(theme.selection, 0)
    border.color: theme.selection
    Behavior on color { ColorAnimation { duration: 120 } }
  }
}
