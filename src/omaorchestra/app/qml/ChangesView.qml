import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A changes.uncommitted() or worktrees.changes() result: a summary, the
// task's commits (for worktrees), untracked files, and a colour-coded diff.
ColumnLayout {
  id: view
  property var result: null
  property bool loading: false
  spacing: 8

  Label {
    visible: view.loading
    color: theme.muted
    text: "Reading changes…"
  }

  Label {
    visible: !view.loading && !!view.result
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: view.result && view.result.error ? theme.urgent : theme.muted
    text: {
      const r = view.result
      if (!r) return ""
      if (r.error) return r.error
      if (!r.repo) return "This folder is not in a git repository."
      const commits = r.commits || []
      if (!r.diff && r.untracked.length === 0 && commits.length === 0) return "No changes."
      const parts = []
      if (r.untracked.length) parts.push(r.untracked.length + " untracked: " + r.untracked.slice(0, 8).join(", ") + (r.untracked.length > 8 ? ", …" : ""))
      if (r.truncated) parts.push("The diff is long; showing the first 200 KB.")
      return parts.join("\n")
    }
  }

  Repeater {
    model: view.result && !view.loading ? (view.result.commits || []) : []
    delegate: Label {
      required property string modelData
      Layout.fillWidth: true
      elide: Text.ElideRight
      text: "󰜘 " + modelData
      color: theme.foreground
      font.pixelSize: 12
    }
  }

  ListView {
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}
    ScrollBar.horizontal: ScrollBar {}
    contentWidth: width * 2
    flickableDirection: Flickable.AutoFlickIfNeeded
    model: view.result && view.result.diff && !view.loading ? view.result.diff.split("\n") : []

    delegate: Label {
      required property string modelData
      text: modelData
      font.pixelSize: 12
      color: modelData.startsWith("+") && !modelData.startsWith("+++") ? theme.accent
             : modelData.startsWith("-") && !modelData.startsWith("---") ? theme.urgent
             : modelData.startsWith("@@") || modelData.startsWith("diff ") ? theme.muted
             : theme.foreground
    }
  }
}
