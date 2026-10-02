import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// What a page shows with nothing in it: a large faint glyph over a line of
// muted text. `theme` comes from Python.
ColumnLayout {
  id: empty
  property string glyph: ""
  property string text: ""
  spacing: 12

  Label {
    Layout.alignment: Qt.AlignHCenter
    visible: empty.glyph !== ""
    text: empty.glyph
    color: theme.muted
    opacity: 0.45
    font.pixelSize: 44
  }
  Label {
    Layout.fillWidth: true
    horizontalAlignment: Text.AlignHCenter
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.muted
    text: empty.text
  }
}
