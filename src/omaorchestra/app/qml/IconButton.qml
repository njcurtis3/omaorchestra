import QtQuick
import QtQuick.Controls

// A glyph button: muted until hovered. `theme` comes from Python.
Label {
  id: icon
  property string glyph: ""
  property string tip: ""
  signal activated()

  text: glyph
  color: mouse.containsMouse ? theme.foreground : theme.muted
  font.pixelSize: 16

  ToolTip.visible: tip !== "" && mouse.containsMouse
  ToolTip.text: tip
  ToolTip.delay: 500

  MouseArea {
    id: mouse
    anchors.fill: parent
    anchors.margins: -4
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: icon.activated()
  }
}
