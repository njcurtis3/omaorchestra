import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Every role a fleet can use, and where it comes from: built in, yours
// (~/.config/omaorchestra/roles), or a project's .claude/agents (seen from
// `folder`). Editing stays in the files: copy one to yours, or open it.
// `fleets` and `theme` come from Python.
ColumnLayout {
  id: rolesView
  property string folder: ""
  property var data_: ({ roles: [], problems: [], dir: "" })
  property string open: ""
  property string message: ""
  function reload() { data_ = fleets.roles(folder) }
  onVisibleChanged: if (visible) reload()
  onFolderChanged: if (visible) reload()
  spacing: 8

  Label {
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: theme.muted
    text: "The first role with a name wins: a project's .claude/agents (" + (rolesView.folder || "no folder picked")
          + "), then yours in " + rolesView.data_.dir + ", then the built-in ones. A role file is a Claude Code subagent file, plus `agent`."
  }
  Label { visible: !!rolesView.message; text: rolesView.message; color: theme.accent; Layout.fillWidth: true; wrapMode: Text.Wrap }
  Repeater {
    model: rolesView.data_.problems
    delegate: Label { required property string modelData; text: "✗ " + modelData; color: theme.urgent; Layout.fillWidth: true; wrapMode: Text.Wrap }
  }

  Repeater {
    model: rolesView.data_.roles
    delegate: Rectangle {
      required property var modelData
      objectName: "role-" + modelData.name
      Layout.fillWidth: true
      implicitHeight: roleBody.implicitHeight + 20
      radius: 6
      color: theme.surface
      ColumnLayout {
        id: roleBody
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
        spacing: 3
        RowLayout {
          Layout.fillWidth: true
          spacing: 12
          Label { text: modelData.name; color: theme.foreground; font.bold: true }
          Label {
            Layout.fillWidth: true
            color: theme.muted
            text: [modelData.agent, modelData.model || "agent's model", modelData.permission_mode || "",
                   modelData.read_only ? "read-only" : "", modelData.source + (modelData.shadows.length ? ", over " + modelData.shadows.join(" and ") : "")]
                  .filter(Boolean).join("  ·  ")
            elide: Text.ElideRight
          }
          FleetButton {
            objectName: "role-copy-" + modelData.name
            visible: modelData.source !== "yours"
            text: "Copy to mine"
            onClicked: {
              const result = fleets.copyRole(modelData.name, rolesView.folder)
              rolesView.message = result.error || result.message
              rolesView.reload()
            }
          }
          FleetButton {
            visible: modelData.source !== "built-in"
            text: "Open"
            onClicked: fleets.openInEditor(modelData.path)
          }
          FleetButton {
            text: rolesView.open === modelData.name ? "Hide prompt" : "Prompt"
            onClicked: rolesView.open = rolesView.open === modelData.name ? "" : modelData.name
          }
        }
        Label { text: modelData.description; color: theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
        Label {
          text: "tools: " + (modelData.tools ? modelData.tools.join(", ") : "all") + (modelData.disallowed_tools.length ? "  ·  not " + modelData.disallowed_tools.join(", ") : "")
          color: theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap
        }
        Label {
          visible: rolesView.open === modelData.name
          text: modelData.prompt
          color: theme.foreground
          font.family: fontFamily
          font.pixelSize: 12
          Layout.fillWidth: true
          wrapMode: Text.Wrap
        }
      }
    }
  }
}
