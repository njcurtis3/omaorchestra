import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// What agents may do without asking (read from their own settings, never
// changed here), and the record of requests for your approval.
// `mcp` (which carries the permissions view) and `theme` come from Python.
ScrollView {
  id: page
  clip: true
  contentWidth: availableWidth
  onVisibleChanged: if (visible) mcp.reload()
  readonly property var p: mcp.permissions

  component Section: Label { color: theme.foreground; font.bold: true; font.pixelSize: 16; topPadding: 10 }
  component Muted: Label { color: theme.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }

  function ago(at) {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - at))
    return s < 3600 ? Math.floor(s / 60) + "m ago" : s < 86400 ? Math.floor(s / 3600) + "h ago" : Math.floor(s / 86400) + "d ago"
  }

  ColumnLayout {
    width: page.availableWidth
    spacing: 6

    Muted { text: "Read from each agent's own settings files; change them there (for Claude Code, /permissions or its settings.json)." }

    Section { text: "Claude Code" }
    Muted { visible: !page.p.claude || page.p.claude.length === 0; text: "No settings files found." }
    Repeater {
      model: page.p.claude || []
      delegate: ColumnLayout {
        required property var modelData
        Layout.fillWidth: true
        spacing: 2
        Label { text: modelData.source; color: theme.foreground }
        Muted { font.pixelSize: 12; text: modelData.path }
        Muted { visible: !!modelData.default_mode; text: "default mode: " + modelData.default_mode }
        Muted {
          readonly property var r: modelData.rules
          text: ["allow", "ask", "deny"].filter(k => r[k].length).map(k => k + ": " + r[k].join(", ")).join("\n") || "(no permission rules)"
          color: theme.muted
        }
        Muted {
          visible: Object.keys(modelData.mcp_lists).length > 0
          text: Object.keys(modelData.mcp_lists).map(k => k + ": " + modelData.mcp_lists[k].join(", ")).join("\n")
        }
      }
    }

    Section { visible: !!(page.p.codex && page.p.codex.length); text: "Codex" }
    Repeater {
      model: page.p.codex || []
      delegate: Muted { required property var modelData; text: "approval policy " + (modelData.approval_policy || "default") + ", sandbox " + (modelData.sandbox_mode || "default") }
    }
    Section { visible: !!(page.p.opencode && page.p.opencode.length); text: "opencode" }
    Repeater {
      model: page.p.opencode || []
      delegate: Muted { required property var modelData; text: JSON.stringify(modelData.permission) }
    }

    Section { text: "Recent requests for your approval" }
    Muted { visible: !page.p.record || page.p.record.length === 0; text: "None recorded yet. Each time an agent waits for you, it is noted here with how it ended." }
    Repeater {
      model: page.p.record || []
      delegate: RowLayout {
        required property var modelData
        objectName: "approval-" + index
        required property int index
        Layout.fillWidth: true
        spacing: 12
        Label { Layout.preferredWidth: 70; text: page.ago(modelData.at); color: theme.muted }
        Label { Layout.preferredWidth: 150; text: (modelData.project || "").split("/").pop(); color: theme.foreground; elide: Text.ElideRight }
        Label {
          Layout.preferredWidth: 190
          text: modelData.outcome + (modelData.waited !== null ? " (" + modelData.waited + "s)" : "")
          color: modelData.outcome === "waiting" ? theme.urgent : theme.muted
        }
        Label { Layout.fillWidth: true; text: modelData.message || ""; color: theme.foreground; elide: Text.ElideRight }
      }
    }
  }
}
