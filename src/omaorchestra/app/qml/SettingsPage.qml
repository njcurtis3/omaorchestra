import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Every setting in config.toml, generated from the config schema. Changes are
// collected until Save, which rewrites only the changed lines (comments stay)
// and asks the daemon to reload. `settings` and `theme` come from Python.
ColumnLayout {
  id: page
  spacing: 12

  // "section.key" -> new value, for settings changed but not saved yet.
  property var edits: ({})
  readonly property bool dirty: Object.keys(edits).length > 0
  property string message: ""
  property bool messageIsError: false

  function valueOf(field) {
    const name = field.section + "." + field.key
    return name in edits ? edits[name] : field.value
  }
  function setValue(field, value) {
    const name = field.section + "." + field.key
    const next = Object.assign({}, edits)
    if (JSON.stringify(value) === JSON.stringify(field.value)) delete next[name]
    else next[name] = value
    edits = next
    message = ""
  }
  function save() {
    const error = settings.save(edits)
    edits = ({})
    messageIsError = error !== ""
    message = error || "Saved. The daemon is using the new settings."
  }

  RowLayout {
    Layout.fillWidth: true
    Label {
      Layout.fillWidth: true
      text: settings.path
      color: theme.muted
      elide: Text.ElideMiddle
    }
    Button {
      text: "Open in editor"
      flat: true
      onClicked: settings.openInEditor()
      contentItem: Label { text: parent.text; color: theme.foreground; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; color: parent.hovered ? theme.selection : "transparent"; border.color: theme.selection }
    }
    Button {
      text: "Revert"
      flat: true
      enabled: page.dirty
      onClicked: { page.edits = ({}); page.message = "" }
      contentItem: Label { text: parent.text; color: parent.enabled ? theme.foreground : theme.muted; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; color: parent.hovered && parent.enabled ? theme.selection : "transparent"; border.color: theme.selection }
    }
    Button {
      objectName: "settings-save"
      text: "Save"
      enabled: page.dirty
      onClicked: page.save()
      contentItem: Label { text: parent.text; color: parent.enabled ? theme.background : theme.muted; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; color: parent.enabled ? theme.accent : "transparent"; border.color: theme.selection }
    }
  }

  Label {
    visible: !!settings.error
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: theme.urgent
    text: "The config file has a problem, so defaults are shown: " + settings.error
  }
  Label {
    visible: !!page.message
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: page.messageIsError ? theme.urgent : theme.accent
    text: page.message
  }

  ScrollView {
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    contentWidth: availableWidth

    ColumnLayout {
      width: parent.width
      spacing: 18

      Repeater {
        model: settings.sections

        delegate: ColumnLayout {
          id: sectionBox
          required property var modelData
          Layout.fillWidth: true
          spacing: 8

          Label { text: sectionBox.modelData.title; color: theme.foreground; font.bold: true; font.pixelSize: 16 }
          Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: sectionBox.modelData.help; color: theme.muted }

          Repeater {
            model: sectionBox.modelData.fields

            delegate: Rectangle {
              id: fieldRow
              required property var modelData
              readonly property var current: page.valueOf(modelData)
              readonly property bool edited: (modelData.section + "." + modelData.key) in page.edits
              Layout.fillWidth: true
              implicitHeight: fieldLayout.implicitHeight + 20
              radius: 6
              color: theme.surface
              border.color: edited ? theme.accent : "transparent"

              RowLayout {
                id: fieldLayout
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
                spacing: 16

                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: 2
                  Label { text: fieldRow.modelData.label; color: theme.foreground }
                  Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: fieldRow.modelData.help + "  (default: " + JSON.stringify(fieldRow.modelData.default) + ")"
                    color: theme.muted
                    font.pixelSize: 12
                  }
                }

                Switch {
                  id: toggle
                  objectName: "setting-" + fieldRow.modelData.section + "." + fieldRow.modelData.key
                  visible: fieldRow.modelData.kind === "bool"
                  checked: fieldRow.modelData.kind === "bool" && fieldRow.current === true
                  onToggled: page.setValue(fieldRow.modelData, checked)
                  // Drawn in theme colours; the Basic style's own is blue.
                  indicator: Rectangle {
                    implicitWidth: 40
                    implicitHeight: 20
                    x: toggle.leftPadding
                    y: (toggle.height - height) / 2
                    radius: 10
                    color: toggle.checked ? theme.accent : theme.selection
                    Rectangle {
                      x: toggle.checked ? parent.width - width - 2 : 2
                      y: 2
                      width: 16; height: 16; radius: 8
                      color: theme.foreground
                      Behavior on x { NumberAnimation { duration: 120 } }
                    }
                  }
                }

                SpinBox {
                  visible: fieldRow.modelData.kind === "int"
                  from: fieldRow.modelData.min || 0
                  to: fieldRow.modelData.max || 0
                  editable: true
                  value: fieldRow.modelData.kind === "int" ? fieldRow.current : 0
                  onValueModified: page.setValue(fieldRow.modelData, value)
                  palette.text: theme.foreground
                  palette.base: theme.background
                  palette.button: theme.selection
                  palette.buttonText: theme.foreground
                }

                Row {
                  visible: fieldRow.modelData.kind === "agents"
                  spacing: 12
                  Repeater {
                    model: fieldRow.modelData.options || []
                    delegate: CheckBox {
                      required property string modelData
                      text: modelData
                      checked: (fieldRow.current || []).indexOf(modelData) >= 0
                      onToggled: {
                        const now = (fieldRow.current || []).filter(a => a !== modelData)
                        if (checked) now.push(modelData)
                        page.setValue(fieldRow.modelData, now)
                      }
                      id: box
                      contentItem: Label { text: box.text; color: theme.foreground; leftPadding: box.indicator.width + 6 }
                      indicator: Rectangle {
                        implicitWidth: 18
                        implicitHeight: 18
                        x: box.leftPadding
                        y: (box.height - height) / 2
                        radius: 3
                        color: box.checked ? theme.accent : "transparent"
                        border.color: box.checked ? theme.accent : theme.muted
                        Label {
                          anchors.centerIn: parent
                          visible: box.checked
                          text: "󰄬"
                          color: theme.background
                          font.pixelSize: 14
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
