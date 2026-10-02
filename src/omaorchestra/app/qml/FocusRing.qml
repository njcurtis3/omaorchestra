import QtQuick

// An accent outline just outside a control while it has keyboard focus
// (Tab), never on a mouse click. Put it in the control's background:
// `FocusRing { control: myButton }`. `theme` comes from Python.
Rectangle {
  property Item control: null
  property real gap: 3
  anchors.fill: parent
  anchors.margins: -gap
  radius: (parent && parent.radius !== undefined ? parent.radius : 4) + gap
  color: "transparent"
  border.color: theme.accent
  border.width: 2
  visible: !!control && control.visualFocus
}
