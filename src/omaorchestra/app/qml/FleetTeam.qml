import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A Claude Code agent team, read-only: its members (the lead and its
// teammates) and its shared task list, as Claude Code keeps them in
// ~/.claude/teams and ~/.claude/tasks. `fleets` and `theme` come from Python.
ColumnLayout {
  id: teamView
  property string runId: ""
  readonly property var t: { fleets.revision; return runId ? fleets.team(runId) : ({ members: [], tasks: [] }) }
  spacing: 8

  Label { text: "Members"; color: theme.muted; font.pixelSize: 12 }
  Flow {
    Layout.fillWidth: true
    spacing: 8
    Repeater {
      model: teamView.t.members
      delegate: Label {
        required property var modelData
        text: modelData.name + (modelData.role && modelData.role !== modelData.name ? "  (" + modelData.role + ")" : "")
        color: modelData.role === "team-lead" ? theme.accent : theme.foreground
        leftPadding: 8; rightPadding: 8; topPadding: 3; bottomPadding: 3
        background: Rectangle { radius: 10; color: theme.surface; border.color: theme.selection }
      }
    }
  }

  Label { text: "Tasks"; color: theme.muted; font.pixelSize: 12; Layout.topMargin: 6 }
  Label { visible: teamView.t.tasks.length === 0; text: "No tasks listed."; color: theme.muted }
  Repeater {
    model: teamView.t.tasks
    delegate: Rectangle {
      required property var modelData
      objectName: "team-task-" + modelData.id
      Layout.fillWidth: true
      implicitHeight: taskBody.implicitHeight + 14
      radius: 4
      color: theme.surface
      RowLayout {
        id: taskBody
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
        spacing: 10
        Label {
          text: modelData.status === "completed" ? "✓" : modelData.status === "in_progress" ? "●" : "○"
          color: modelData.status === "in_progress" ? theme.accent : theme.foreground
        }
        Label { Layout.fillWidth: true; text: modelData.subject || modelData.id; color: theme.foreground; wrapMode: Text.Wrap }
        Label {
          text: [modelData.owner, modelData.blockedBy.length ? "after " + modelData.blockedBy.join(", ") : ""].filter(Boolean).join("  ·  ")
          color: theme.muted
          font.pixelSize: 12
        }
      }
    }
  }
}
