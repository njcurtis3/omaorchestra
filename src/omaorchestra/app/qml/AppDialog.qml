import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A modal question in the app's colours, answered with its own buttons (the
// Basic style's standard ones are grey): `acceptText` (filled, or red when
// `danger`) and Cancel. Put the body in `contentItem` (a label: `width:
// bodyWidth`). `theme` comes from Python.
Dialog {
  id: dialog
  property string acceptText: "OK"
  property bool danger: false
  property bool acceptEnabled: true

  anchors.centerIn: Overlay.overlay
  modal: true
  // Fixed: the smallest window (640) fits it, and a width taken from the
  // window or page loops (Qt reads it while working out the height).
  width: 460
  // The body's width: give a wrapping label this as its width, since
  // one left to the dialog loops (its height depends on its width).
  readonly property real bodyWidth: width - leftPadding - rightPadding
  padding: 16
  topPadding: 0

  background: Rectangle { color: theme.surface; radius: 8; border.color: dialog.danger ? theme.urgent : theme.selection }
  header: Label {
    text: dialog.title
    padding: 16
    color: theme.foreground
    font.bold: true
    elide: Text.ElideRight
  }
  footer: Item {
    implicitHeight: buttons.implicitHeight + 16
    RowLayout {
      id: buttons
      anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 16; rightMargin: 16 }
      spacing: 8
      Item { Layout.fillWidth: true }
      AppButton {
        objectName: dialog.objectName ? dialog.objectName + "-cancel" : ""
        text: "Cancel"
        onClicked: dialog.reject()
      }
      AppButton {
        objectName: dialog.objectName ? dialog.objectName + "-ok" : ""
        text: dialog.acceptText
        primary: !dialog.danger
        danger: dialog.danger
        enabled: dialog.acceptEnabled
        onClicked: dialog.accept()
      }
    }
  }
}
