import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Task worktrees: how each stands against where it started, with review,
// merge and remove. Worktrees outlive their sessions, so they have their own
// page. `worktrees`, `sessions` and `theme` come from Python.
ColumnLayout {
  id: page
  signal openFleet(string runId)
  spacing: 12

  property string reviewing: ""   // path of the worktree whose changes are shown
  property var reviewResult: null
  property bool reviewLoading: false
  property string message: ""
  property bool messageIsError: false
  property var pending: null      // {action, path, force} awaiting confirmation

  onVisibleChanged: if (visible) worktrees.refresh()

  function report(result) {
    messageIsError = !!result.error
    message = result.error || result.message
  }
  function review(path) {
    reviewing = path
    reviewLoading = true
    reviewResult = null
    worktrees.requestChanges(path)
  }

  Connections {
    target: worktrees
    function onChangesReady(path, result) {
      if (path !== page.reviewing) return
      page.reviewResult = result
      page.reviewLoading = false
    }
  }

  RowLayout {
    Layout.fillWidth: true
    Label {
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.muted
      text: page.reviewing
        ? "Everything done in this worktree since its task started."
        : "Each task started with a separate worktree works on its own branch. Review what it did, merge it into the branch it started from, then remove it."
    }
    IconButton { Layout.alignment: Qt.AlignTop; visible: !!page.reviewing; glyph: "󰁍"; tip: "Back to the list"; onActivated: page.reviewing = "" }
    IconButton { Layout.alignment: Qt.AlignTop; glyph: "󰑐"; tip: "Refresh"; onActivated: page.reviewing ? page.review(page.reviewing) : worktrees.refresh() }
  }

  Label {
    visible: !!page.message
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: page.messageIsError ? theme.urgent : theme.accent
    text: page.message
  }

  ChangesView {
    visible: !!page.reviewing
    Layout.fillWidth: true
    Layout.fillHeight: true
    result: page.reviewResult
    loading: page.reviewLoading
  }

  Label {
    visible: !page.reviewing && worktrees.items.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 24
    horizontalAlignment: Text.AlignHCenter
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.muted
    text: "No task worktrees. Start a task in a git repository with a separate worktree to get one."
  }

  ListView {
    visible: !page.reviewing
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: 6
    model: worktrees.items
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Rectangle {
      id: item
      required property var modelData
      objectName: "worktree-" + modelData.branch
      readonly property bool live: sessions.rows.some(s => s.worktree === modelData.path)
      width: ListView.view.width
      height: itemContent.implicitHeight + 20
      radius: 6
      color: theme.surface

      RowLayout {
        id: itemContent
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
        spacing: 14

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3
          RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Label {
              Layout.fillWidth: true
              Layout.maximumWidth: Math.min(Math.ceil(implicitWidth), 420)
              text: item.modelData.task
              color: theme.foreground
              font.bold: true
              elide: Text.ElideRight
            }
            Label { visible: item.live; text: "agent running"; color: theme.accent; font.pixelSize: 12 }
            Label {
              objectName: "worktree-fleet-" + item.modelData.branch
              visible: !!item.modelData.fleet
              text: "omafleet " + (item.modelData.slice ? "slice " + item.modelData.slice : "run") + " ›"
              color: theme.accent
              font.pixelSize: 12
              MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.openFleet(item.modelData.fleet) }
            }
            Item { Layout.fillWidth: true }
          }
          Label {
            Layout.fillWidth: true
            elide: Text.ElideMiddle
            text: " " + item.modelData.branch + "  →  " + (item.modelData.base_branch || item.modelData.base.slice(0, 8))
            color: theme.muted
            font.pixelSize: 12
          }
          Label {
            Layout.fillWidth: true
            text: item.modelData.path
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideMiddle
          }
        }

        // At most a third of the row; its lines elide past that.
        ColumnLayout {
          Layout.alignment: Qt.AlignTop
          Layout.fillWidth: true
          Layout.maximumWidth: Math.min(Math.ceil(implicitWidth), itemContent.width / 3)
          spacing: 2
          Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideRight
            color: item.modelData.error ? theme.urgent : item.modelData.merged ? theme.accent : theme.foreground
            text: item.modelData.error ? "error"
              : !item.modelData.exists ? "folder gone"
              : item.modelData.merged ? "merged"
              : item.modelData.commitCount + " commit" + (item.modelData.commitCount === 1 ? "" : "s")
                + (item.modelData.dirty ? " · uncommitted changes" : "")
          }
          // A review step's verdict (chain.py); click for the findings.
          Label {
            objectName: "review-" + item.modelData.branch
            visible: !!item.modelData.review
            Layout.alignment: Qt.AlignRight
            Layout.maximumWidth: parent.width
            elide: Text.ElideRight
            text: item.modelData.review ? "reviewed: " + item.modelData.review.verdict : ""
            color: !item.modelData.review ? theme.muted
                   : item.modelData.review.verdict === "ready" ? theme.accent
                   : item.modelData.review.verdict === "needs work" ? theme.urgent : theme.muted
            font.underline: reviewMouse.containsMouse
            MouseArea {
              id: reviewMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: Qt.openUrlExternally("file://" + item.modelData.review.file)
            }
            ToolTip.visible: reviewMouse.containsMouse
            ToolTip.text: "Open the review's findings"
          }
        }

        Row {
          Layout.alignment: Qt.AlignTop
          spacing: 12
          IconButton { glyph: "󰈈"; tip: "Review changes"; visible: item.modelData.exists; onActivated: page.review(item.modelData.path) }
          IconButton { glyph: "󰉋"; tip: "Open folder"; visible: item.modelData.exists; onActivated: sessions.openFolder(item.modelData.path) }
          IconButton {
            objectName: "merge-" + item.modelData.branch
            glyph: "󰘬"
            tip: "Merge into " + (item.modelData.base_branch || "its base")
            visible: item.modelData.exists && !item.modelData.merged && item.modelData.commitCount > 0
            onActivated: { page.pending = { action: "merge", path: item.modelData.path, force: false, task: item.modelData.task, target: item.modelData.base_branch }; confirm.open() }
          }
          IconButton {
            objectName: "remove-" + item.modelData.branch
            glyph: "󰆴"
            tip: "Remove the worktree"
            onActivated: {
              const losing = item.modelData.exists && (item.modelData.dirty || (item.modelData.commitCount > 0 && !item.modelData.merged))
              page.pending = { action: "remove", path: item.modelData.path, force: losing, task: item.modelData.task,
                               fleet: item.modelData.fleet || "" }
              confirm.open()
            }
          }
        }
      }
    }
  }

  Dialog {
    id: confirm
    objectName: "worktree-confirm"
    anchors.centerIn: Overlay.overlay
    modal: true
    standardButtons: Dialog.Cancel | Dialog.Ok
    title: page.pending && page.pending.action === "merge" ? "Merge this task?" : "Remove this worktree?"
    onAccepted: {
      const p = page.pending
      if (!p) return
      page.report(p.action === "merge" ? worktrees.merge(p.path) : worktrees.remove(p.path, p.force))
      page.pending = null
    }
    background: Rectangle { color: theme.surface; radius: 8; border.color: page.pending && page.pending.force ? theme.urgent : theme.selection }
    header: Label { text: confirm.title; padding: 16; color: theme.foreground; font.bold: true }
    contentItem: Label {
      width: 420
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: page.pending && page.pending.force ? theme.urgent : theme.foreground
      text: {
        const p = page.pending
        if (!p) return ""
        if (p.action === "merge")
          return "Merge \"" + p.task + "\" into " + p.target + " in the main checkout. It must be clean and on " + p.target + "; a conflicting merge is undone."
        const fleet = p.fleet ? " It belongs to fleet run " + p.fleet + ": closing the run removes its slices' worktrees once their work is on the run's branch, so removing it by hand can hold or break the run." : ""
        if (p.force)
          return "\"" + p.task + "\" has work that is not merged. Removing it deletes that work and its branch for good." + fleet
        return "Remove the worktree for \"" + p.task + "\" (its branch goes too once merged)." + fleet
      }
    }
  }
}
