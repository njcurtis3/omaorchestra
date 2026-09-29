import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The fleets a run can use: auto, single-loop, diamond, and yours in
// fleets.toml, each with the shape it runs and who plays which stage.
// `fleets` and `theme` come from Python.
ColumnLayout {
  id: templatesView
  property var data_: ({ templates: [], error: "", path: "" })
  function reload() { data_ = fleets.templates() }
  onVisibleChanged: if (visible) reload()
  spacing: 8

  RowLayout {
    Layout.fillWidth: true
    Label {
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.muted
      text: "Yours go in " + templatesView.data_.path + ". A fleet sets the shape, whether to scout, how many tries, a budget and step limit, and which role plays each stage."
    }
    AppButton { objectName: "templates-edit"; text: "Open fleets.toml"; onClicked: { fleets.openInEditor(""); templatesView.reload() } }
  }
  Label { visible: !!templatesView.data_.error; text: templatesView.data_.error; color: theme.urgent; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }

  Repeater {
    model: templatesView.data_.templates
    delegate: Rectangle {
      required property var modelData
      objectName: "template-" + modelData.name
      Layout.fillWidth: true
      implicitHeight: body.implicitHeight + 20
      radius: 6
      color: theme.surface
      ColumnLayout {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
        spacing: 3
        RowLayout {
          Label { text: modelData.name; color: theme.foreground; font.bold: true }
          Label { text: modelData.builtin ? "built in" : "yours"; color: theme.muted; font.pixelSize: 12 }
        }
        Label { text: modelData.description; color: theme.foreground; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
        Label { text: modelData.drawing; color: theme.accent; font.family: fontFamily; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
        Label {
          color: theme.muted
          font.pixelSize: 12
          Layout.fillWidth: true
          wrapMode: Text.WrapAtWordBoundaryOrAnywhere
          text: [modelData.scout ? "" : "no scout", "tries " + modelData.tries, modelData.budget ? "budget $" + modelData.budget : "no budget",
                 "up to " + modelData.max_steps + " steps", "stalled after " + modelData.stall_minutes + " min",
                 Object.keys(modelData.roles).filter(k => modelData.roles[k] !== k).map(k => k + ": " + modelData.roles[k]).join(", ")]
                .filter(Boolean).join("  ·  ")
        }
      }
    }
  }
}
