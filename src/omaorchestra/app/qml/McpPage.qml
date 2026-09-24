import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// MCP servers across agents, their health, the servers omaorchestra
// manages, and profiles. Adding servers (which asks for secrets) is done
// with `omaorchestra mcp add`. `mcp` and `theme` come from Python.
ScrollView {
  id: page
  clip: true
  contentWidth: availableWidth
  property string message: ""

  onVisibleChanged: if (visible) mcp.reload()

  function act(error) { message = error }

  component Section: Label { color: theme.foreground; font.bold: true; font.pixelSize: 16; topPadding: 10 }
  component Muted: Label { color: theme.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
  component PlainButton: Button {
    flat: true
    contentItem: Label { text: parent.text; color: parent.enabled ? theme.foreground : theme.muted; horizontalAlignment: Text.AlignHCenter }
    background: Rectangle { radius: 4; implicitWidth: 70; color: parent.hovered && parent.enabled ? theme.selection : "transparent"; border.color: theme.selection }
  }

  ColumnLayout {
    width: page.availableWidth
    spacing: 6

    RowLayout {
      Layout.fillWidth: true
      Muted { text: "Every MCP server configured for Claude Code, Codex and opencode on this machine. Values of secrets are never shown. Add servers with `omaorchestra mcp add` (it asks for secrets and keeps them in the keyring)." }
      PlainButton {
        objectName: "mcp-check"
        text: mcp.checking ? "Checking…" : "Check all"
        enabled: !mcp.checking && mcp.servers.length > 0
        onClicked: mcp.checkAll()
      }
    }
    Label { visible: !!page.message || !!mcp.error; color: theme.urgent; text: page.message || mcp.error; wrapMode: Text.Wrap; Layout.fillWidth: true }

    Section { text: "Servers" }
    Muted { visible: mcp.servers.length === 0; text: "No MCP servers configured. (claude.ai connectors live in your account, not on this machine, and are not listed.)" }
    Repeater {
      model: mcp.servers
      delegate: Rectangle {
        id: row
        required property var modelData
        objectName: "mcp-server-" + modelData.agent + "-" + modelData.name
        readonly property var h: modelData.health
        Layout.fillWidth: true
        implicitHeight: rowContent.implicitHeight + 16
        radius: 6
        color: theme.surface

        RowLayout {
          id: rowContent
          anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
          spacing: 12
          Rectangle {
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 5
            width: 10; height: 10; radius: 5
            color: !row.h.checked ? theme.muted : row.h.ok ? theme.accent : theme.urgent
          }
          ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            RowLayout {
              spacing: 10
              Label { text: row.modelData.name; color: theme.foreground; font.bold: true }
              Label { text: row.modelData.agent + " · " + row.modelData.scope + " · " + row.modelData.transport; color: theme.muted }
              Label { visible: !row.modelData.enabled; text: "disabled"; color: theme.urgent }
              Label { visible: row.modelData.managed; text: "managed by omaorchestra"; color: theme.accent }
            }
            Muted {
              font.pixelSize: 12
              elide: Text.ElideMiddle
              wrapMode: Text.NoWrap
              text: (row.modelData.url || [row.modelData.command].concat(row.modelData.args).join(" "))
                + (row.modelData.project ? "   (" + row.modelData.project + ")" : "")
            }
            Muted {
              visible: row.modelData.env_keys.length + row.modelData.header_keys.length > 0
              font.pixelSize: 12
              text: "uses " + row.modelData.env_keys.concat(row.modelData.header_keys).join(", ") + " (values not shown)"
            }
            Label {
              visible: !!row.modelData.healthText
              Layout.fillWidth: true
              wrapMode: Text.Wrap
              font.pixelSize: 12
              color: row.h.ok ? theme.muted : theme.urgent
              text: row.h.ok
                ? row.modelData.healthText + ": " + row.h.tools.map(t => t.name).slice(0, 12).join(", ") + (row.h.tools.length > 12 ? " …" : "")
                : row.modelData.healthText
            }
          }
        }
      }
    }

    Section { text: "Managed by omaorchestra" }
    Muted { visible: Object.keys(mcp.managed).length === 0; text: "None yet." }
    Repeater {
      model: Object.keys(mcp.managed)
      delegate: RowLayout {
        required property string modelData
        readonly property var spec: mcp.managed[modelData]
        Layout.fillWidth: true
        spacing: 10
        Label { Layout.preferredWidth: 160; text: modelData; color: theme.foreground }
        Muted {
          text: (spec.enabled ? "" : "disabled · ") + (spec.targets.length ? "in " + spec.targets.map(t => t.agent + (t.agent === "claude" ? " " + t.scope : "")).join(", ") : "profiles only")
            + ((spec.secret_env || []).concat(spec.secret_headers || []).length ? " · secrets in keyring: " + (spec.secret_env || []).concat(spec.secret_headers || []).join(", ") : "")
        }
        PlainButton { text: spec.enabled ? "Disable" : "Enable"; visible: spec.targets.length > 0; onClicked: page.act(mcp.setEnabled(modelData, !spec.enabled)) }
        PlainButton { text: "Remove"; onClicked: page.act(mcp.removeServer(modelData)) }
      }
    }

    Section { text: "Profiles" }
    Muted { text: "A profile starts a task with exactly its servers (New task → MCP servers), instead of the agent's own. \"None\" is built in. Only managed servers can be in a profile." }
    Repeater {
      model: Object.keys(mcp.profiles)
      delegate: RowLayout {
        required property string modelData
        Layout.fillWidth: true
        spacing: 10
        Label { Layout.preferredWidth: 160; text: modelData; color: theme.foreground }
        Muted { text: mcp.profiles[modelData].join(", ") || "(no servers)" }
        PlainButton { text: "Remove"; onClicked: page.act(mcp.removeProfile(modelData)) }
      }
    }
    RowLayout {
      visible: Object.keys(mcp.managed).length > 0
      spacing: 8
      TextField {
        id: profileName
        objectName: "mcp-profile-name"
        Layout.preferredWidth: 160
        placeholderText: "new profile"
        placeholderTextColor: theme.muted
        color: theme.foreground
        background: Rectangle { radius: 4; color: theme.surface; border.color: theme.selection }
      }
      Repeater {
        id: memberChecks
        model: Object.keys(mcp.managed)
        delegate: CheckBox {
          required property string modelData
          objectName: "mcp-member-" + modelData
          text: modelData
          contentItem: Label { text: parent.text; color: theme.foreground; leftPadding: parent.indicator.width + 6 }
        }
      }
      PlainButton {
        objectName: "mcp-profile-save"
        text: "Save profile"
        enabled: profileName.text.trim() !== ""
        onClicked: {
          const members = []
          for (let i = 0; i < memberChecks.count; i++) {
            const box = memberChecks.itemAt(i)
            if (box.checked) members.push(box.text)
          }
          page.act(mcp.setProfile(profileName.text.trim(), members))
          if (!page.message) profileName.text = ""
        }
      }
    }
  }
}
