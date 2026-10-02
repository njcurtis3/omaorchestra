import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One session up close: what it is doing now (the transcript's latest
// prompts, replies and tool calls), how its status has changed, and the
// uncommitted changes in its folder. `sessions` and `theme` come from Python.
ColumnLayout {
  id: detail
  property string sessionId: ""
  property real now: Date.now() / 1000
  signal back()

  spacing: 12

  // Re-read whenever sessions change (sessions.rows notifies).
  readonly property var s: { sessions.rows; return sessions.row(sessionId) }
  readonly property bool gone: !s || !s.id
  property var changesResult: null
  property bool changesLoading: false
  property string actionError: ""

  function statusColor(status) {
    return status === "needs-input" ? theme.urgent : status === "working" ? theme.accent : theme.muted
  }
  function loadChanges() {
    changesLoading = true
    sessions.requestChanges(sessionId)
  }

  Connections {
    target: sessions
    function onChangesReady(id, result) {
      if (id !== detail.sessionId) return
      detail.changesResult = result
      detail.changesLoading = false
    }
  }

  Shortcut { sequence: "Escape"; enabled: detail.visible; onActivated: detail.back() }

  // ---------------------------------------------------------- Header
  // The buttons beside the title, or under it in a narrow window (the
  // window's width decides: see Main.pageWidth).
  GridLayout {
    Layout.fillWidth: true
    columns: (ApplicationWindow.window ? ApplicationWindow.window.pageWidth : detail.width) >= 760 ? 2 : 1
    columnSpacing: 12
    rowSpacing: 8

  RowLayout {
    Layout.fillWidth: true
    spacing: 12

    IconButton { glyph: "󰁍"; tip: "Back to sessions (Esc)"; onActivated: detail.back() }
    Rectangle { visible: !detail.gone; width: 10; height: 10; radius: 5; color: detail.statusColor(detail.s.status) }
    Label {
      Layout.fillWidth: true
      Layout.maximumWidth: Math.ceil(implicitWidth)
      Layout.minimumWidth: Math.min(Math.ceil(implicitWidth), 100)
      text: detail.gone ? "Session ended" : detail.s.project
      color: theme.foreground
      font.pixelSize: 18
      font.bold: true
      elide: Text.ElideRight
    }
    Label {
      visible: !detail.gone && !!detail.s.title
      Layout.fillWidth: true
      text: detail.gone ? "" : (detail.s.title || "")
      color: theme.muted
      elide: Text.ElideRight
    }
    Item { visible: detail.gone || !detail.s.title; Layout.fillWidth: true }
  }

    Row {
      visible: !detail.gone
      Layout.alignment: Qt.AlignRight
      spacing: 8
      Repeater {
        model: [
          { label: "Focus", tip: "Jump to its terminal", action: "focus" },
          { label: "Hand off…", tip: "Start another agent on this work, with a brief", action: "handoff" },
          { label: "Stop", tip: "End the agent process", action: "stop" },
          { label: "Dismiss", tip: "Remove from the list (returns if the agent reports again)", action: "dismiss" }
        ]
        delegate: AppButton {
          required property var modelData
          objectName: "detail-" + modelData.action
          text: modelData.label
          danger: modelData.action === "stop"
          ToolTip.visible: hovered
          ToolTip.text: modelData.tip
          ToolTip.delay: 500
          onClicked: {
            detail.actionError = ""
            if (modelData.action === "focus") sessions.focus(detail.sessionId)
            else if (modelData.action === "stop") stopDialog.open()
            else if (modelData.action === "handoff") handoffMenu.popup()
            else { sessions.dismiss(detail.sessionId); detail.back() }
          }
        }
      }
    }
  }

  Label {
    visible: detail.gone
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.muted
    text: "This session has ended or was dismissed."
  }

  Label {
    visible: !detail.gone
    Layout.fillWidth: true
    elide: Text.ElideMiddle
    color: theme.muted
    text: detail.gone ? "" : [
      detail.s.statusLabel + " for " + sessions.duration(detail.s.since, detail.now),
      detail.s.branch ? " " + detail.s.branch : "",
      detail.s.worktree ? "󰙅 own worktree" : "",
      detail.s.modelName || "",
      sessions.costText(detail.sessionId),
      detail.s.place || detail.s.cwd || ""
    ].filter(Boolean).join("   ·   ")
  }

  Label {
    visible: !!detail.actionError
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.urgent
    text: detail.actionError
  }

  Rectangle {
    visible: !detail.gone && detail.s.status === "needs-input" && !!detail.s.message
    Layout.fillWidth: true
    implicitHeight: waitingText.implicitHeight + 20
    radius: 6
    color: Qt.alpha(theme.urgent, 0.10)
    border.color: Qt.alpha(theme.urgent, 0.3)
    Stripe { color: theme.urgent }
    Label {
      id: waitingText
      anchors.fill: parent
      anchors.margins: 10
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.urgent
      text: detail.gone ? "" : (detail.s.message || "")
    }
  }

  // ---------------------------------------------------------- Tabs
  TabBar {
    id: tabs
    visible: !detail.gone
    Layout.fillWidth: true
    background: Rectangle { color: "transparent" }
    onCurrentIndexChanged: if (currentIndex === 2 && !detail.changesResult) detail.loadChanges()

    Repeater {
      model: ["Activity", "Timeline", "Changes"]
      delegate: TabButton {
        required property string modelData
        required property int index
        text: modelData
        width: implicitWidth + 24
        contentItem: Label {
          text: parent.text
          color: tabs.currentIndex === index ? theme.foreground : theme.muted
          horizontalAlignment: Text.AlignHCenter
        }
        background: Rectangle {
          color: "transparent"
          Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: tabs.currentIndex === index ? theme.accent : "transparent" }
        }
      }
    }
  }

  StackLayout {
    visible: !detail.gone
    Layout.fillWidth: true
    Layout.fillHeight: true
    currentIndex: tabs.currentIndex

    // Activity
    ListView {
      id: activityList
      clip: true
      spacing: 8
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar {}
      model: { sessions.rows; return detail.gone ? [] : sessions.activity(detail.sessionId) }
      onCountChanged: positionViewAtEnd()

      delegate: RowLayout {
        required property var modelData
        width: ListView.view.width - 12
        spacing: 12
        Label {
          Layout.alignment: Qt.AlignTop
          Layout.fillWidth: true
          Layout.preferredWidth: 70
          Layout.maximumWidth: 70
          elide: Text.ElideRight
          text: sessions.isoClock(modelData.at)
          color: theme.muted
          font.pixelSize: 12
        }
        Label {
          Layout.alignment: Qt.AlignTop
          Layout.fillWidth: true
          Layout.preferredWidth: 20
          Layout.maximumWidth: 20
          elide: Text.ElideRight
          text: modelData.kind === "prompt" ? "󰍩" : modelData.kind === "tool" ? "󰒓" : "󰚩"
          color: modelData.kind === "prompt" ? theme.accent : theme.muted
        }
        Label {
          Layout.fillWidth: true
          wrapMode: Text.WrapAtWordBoundaryOrAnywhere
          text: modelData.text
          color: modelData.kind === "tool" ? theme.muted : theme.foreground
        }
      }

      Label {
        anchors.centerIn: parent
        visible: activityList.count === 0
        color: theme.muted
        text: "No activity to show yet."
      }
    }

    // Timeline
    ListView {
      clip: true
      spacing: 6
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar {}
      model: { sessions.rows; return detail.gone ? [] : sessions.timeline(detail.sessionId) }

      delegate: RowLayout {
        required property var modelData
        width: ListView.view.width - 12
        spacing: 12
        Label { Layout.fillWidth: true; Layout.preferredWidth: 70; Layout.maximumWidth: 70; elide: Text.ElideRight; text: sessions.clock(modelData.at); color: theme.muted; font.pixelSize: 12 }
        Rectangle { width: 10; height: 10; radius: 5; color: detail.statusColor(modelData.status) }
        Label { Layout.fillWidth: true; Layout.preferredWidth: 90; Layout.maximumWidth: 90; elide: Text.ElideRight; text: modelData.label; color: theme.foreground }
        Label {
          Layout.fillWidth: true
          color: theme.muted
          text: modelData.until
            ? "for " + sessions.duration(modelData.at, modelData.until)
            : "for " + sessions.duration(modelData.at, detail.now) + " (now)"
        }
      }
    }

    // Changes
    ColumnLayout {
      spacing: 8

      RowLayout {
        Layout.fillWidth: true
        Label {
          Layout.fillWidth: true
          wrapMode: Text.WrapAtWordBoundaryOrAnywhere
          color: theme.muted
          text: detail.gone || !detail.s.worktree
            ? "Uncommitted changes in this folder, whoever made them."
            : "Everything this task has done in its own worktree since it started, commits included."
        }
        IconButton { glyph: "󰑐"; tip: "Refresh"; onActivated: detail.loadChanges() }
      }

      ChangesView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        result: detail.changesResult
        loading: detail.changesLoading
      }
    }
  }

  // ---------------------------------------------------------- Hand-off
  Menu {
    id: handoffMenu
    objectName: "handoff-menu"
    Repeater {
      model: sessions.agentChoices()
      delegate: MenuItem {
        required property var modelData
        objectName: "handoff-to-" + modelData.value
        text: "Hand off to " + modelData.label
        onTriggered: {
          const error = sessions.handoff(detail.sessionId, modelData.value)
          detail.actionError = error
        }
      }
    }
  }

  // ---------------------------------------------------------- Stop confirmation
  AppDialog {
    id: stopDialog
    objectName: "stop-dialog"
    title: "Stop this agent?"
    acceptText: "Stop it"
    danger: true
    onAccepted: detail.actionError = sessions.stop(detail.sessionId)

    contentItem: Label {
      width: stopDialog.bodyWidth
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.foreground
      text: "This ends the agent process for " + (detail.gone ? "this session" : detail.s.project)
        + ". Its conversation stays in its transcript, but any work in progress stops."
    }
  }
}
