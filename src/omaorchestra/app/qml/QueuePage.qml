import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Tasks waiting for a free agent slot, in the order they will start.
// `queue` and `theme` come from Python.
ColumnLayout {
  id: page
  spacing: 12

  property string message: ""

  function act(result) { message = result.error || "" }

  RowLayout {
    Layout.fillWidth: true
    Label {
      Layout.fillWidth: true
      color: theme.muted
      text: queue.busy + " of " + queue.limit + " agent slots busy"
        + (queue.held ? " · queue held: nothing new starts" : "")
        + ". A slot frees when an agent finishes; waiting for you still counts as busy."
      wrapMode: Text.Wrap
    }
    Button {
      objectName: "queue-hold"
      text: queue.held ? "Release" : "Hold"
      flat: true
      onClicked: page.act(queue.setHeld(!queue.held))
      contentItem: Label { text: parent.text; color: queue.held ? theme.accent : theme.foreground; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; implicitWidth: 90; color: parent.hovered ? theme.selection : "transparent"; border.color: queue.held ? theme.accent : theme.selection }
    }
  }

  Rectangle {
    objectName: "queue-blocked"
    visible: !!queue.blockedText
    Layout.fillWidth: true
    implicitHeight: blockedLabel.implicitHeight + 20
    radius: 6
    color: Qt.alpha(theme.urgent, 0.12)
    border.color: theme.urgent
    Label {
      id: blockedLabel
      anchors.fill: parent
      anchors.margins: 10
      wrapMode: Text.Wrap
      color: theme.urgent
      text: "Waiting: " + queue.blockedText + ". Queued tasks start once it is below the limit set in Settings (Start now overrides it)."
    }
  }

  Label {
    visible: !!page.message
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: theme.urgent
    text: page.message
  }

  Label {
    visible: queue.tasks.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 24
    horizontalAlignment: Text.AlignHCenter
    color: theme.muted
    text: "No queued tasks. Use Add to queue on the New task page, or `omaorchestra queue add`."
  }

  ListView {
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: 6
    model: queue.tasks
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Rectangle {
      id: row
      required property var modelData
      required property int index
      objectName: "queued-" + index
      width: ListView.view.width
      height: rowContent.implicitHeight + 20
      radius: 6
      color: theme.surface
      border.color: modelData.state === "failed" ? theme.urgent : "transparent"
      opacity: modelData.state === "paused" ? 0.6 : 1

      RowLayout {
        id: rowContent
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
        spacing: 14

        Label {
          Layout.alignment: Qt.AlignTop
          text: (row.index + 1) + "."
          color: theme.muted
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3
          Label { Layout.fillWidth: true; text: row.modelData.task; color: theme.foreground; elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap }
          Label {
            Layout.fillWidth: true
            text: [row.modelData.cwd, row.modelData.model || "", row.modelData.worktree === false ? "no worktree" : ""].filter(Boolean).join("   ·   ")
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideMiddle
          }
          Label {
            visible: !!row.modelData.error
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "Failed to start: " + (row.modelData.error || "")
            color: theme.urgent
            font.pixelSize: 12
          }
        }

        Label {
          Layout.alignment: Qt.AlignTop
          text: row.modelData.state === "pending" ? (queue.held ? "held" : queue.blockedText ? "usage limit" : "waiting")
                : row.modelData.state
          color: row.modelData.state === "failed" ? theme.urgent : theme.muted
        }

        Row {
          Layout.alignment: Qt.AlignTop
          spacing: 12
          IconButton { glyph: "󰁝"; tip: "Move up"; visible: row.index > 0; onActivated: page.act(queue.move(row.modelData.id, row.index - 1)) }
          IconButton { glyph: "󰁅"; tip: "Move down"; visible: row.index < queue.tasks.length - 1; onActivated: page.act(queue.move(row.modelData.id, row.index + 1)) }
          IconButton {
            objectName: "queued-pause-" + row.index
            glyph: row.modelData.state === "pending" ? "󰏤" : "󰐊"
            tip: row.modelData.state === "pending" ? "Pause" : row.modelData.state === "failed" ? "Retry" : "Resume"
            onActivated: page.act(queue.setPaused(row.modelData.id, row.modelData.state === "pending"))
          }
          IconButton { objectName: "queued-run-" + row.index; glyph: "󰑮"; tip: "Start now, whatever the limit"; onActivated: page.act(queue.runNow(row.modelData.id)) }
          IconButton { objectName: "queued-cancel-" + row.index; glyph: "󰅖"; tip: "Remove from the queue"; onActivated: page.act(queue.cancel(row.modelData.id)) }
        }
      }
    }
  }
}
