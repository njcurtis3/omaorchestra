import QtQuick
import QtQuick.Controls

// The app's one dropdown, 32 high like its buttons and text fields: outlined
// like AppButton, filled under the pointer, its list in theme colours, and
// wide enough for the longest choice. `theme` comes from Python.
ComboBox {
  id: combo
  implicitContentWidthPolicy: ComboBox.WidestTextWhenCompleted
  implicitHeight: 32
  palette.button: theme.surface
  palette.buttonText: theme.foreground
  palette.base: theme.surface
  palette.text: theme.foreground
  palette.window: theme.surface
  palette.windowText: theme.foreground
  palette.highlight: theme.selection
  palette.highlightedText: theme.foreground
  palette.mid: theme.selection
  palette.dark: theme.muted
  palette.toolTipBase: theme.surface
  palette.toolTipText: theme.foreground
  // Editable: the text field draws no box of its own inside this one.
  Binding { target: combo.contentItem; property: "background"; value: null; when: combo.editable }
  background: Rectangle {
    radius: 4
    implicitWidth: 120
    implicitHeight: 32
    color: combo.hovered && combo.enabled || combo.popup.visible ? theme.selection : theme.surface
    border.color: combo.popup.visible ? theme.accent : theme.selection
    opacity: combo.enabled ? 1 : 0.6
    Behavior on color { ColorAnimation { duration: 120 } }
    FocusRing { control: combo }
  }
}
