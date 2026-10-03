import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Schedules, then the tasks waiting for a free agent slot, in the order they
// will start. A chained task (the next step of a chain) is indented under the
// one it follows and waits for it to finish. `queue`, `schedules` and `theme`
// come from Python.
ColumnLayout {
  id: page
  spacing: 12
  signal openFleet(string runId)

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
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    }
    AppButton {
      objectName: "queue-hold"
      Layout.alignment: Qt.AlignTop
      text: queue.held ? "Release" : "Hold"
      active: queue.held
      onClicked: page.act(queue.setHeld(!queue.held))
    }
  }

  Rectangle {
    objectName: "queue-blocked"
    visible: !!queue.blockedText
    Layout.fillWidth: true
    implicitHeight: blockedLabel.implicitHeight + 20
    radius: 6
    color: Qt.alpha(theme.urgent, 0.10)
    border.color: Qt.alpha(theme.urgent, 0.3)
    Stripe { color: theme.urgent }
    Label {
      id: blockedLabel
      anchors.fill: parent
      anchors.margins: 10
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.urgent
      text: "Waiting: " + queue.blockedText + ". Queued tasks start once it is below the limit set in Settings (Start now overrides it)."
    }
  }

  Label {
    visible: !!page.message
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.urgent
    text: page.message
  }

  // ---------------------------------------------------------- Schedules
  // Tasks that queue themselves on a timetable; what they queue joins the
  // list below when its time comes.
  component Heading: Label { color: theme.foreground; font.bold: true; font.pixelSize: 16 }

  Heading { visible: schedules.rows.length > 0; text: "Schedules" }
  Label {
    objectName: "schedules-off"
    visible: schedules.rows.length > 0 && !schedules.enabled
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.urgent
    text: "Schedules are off in Settings: none queues anything until they are turned back on."
  }
  ColumnLayout {
    Layout.fillWidth: true
    spacing: 6
  Repeater {
    model: schedules.rows
    delegate: Rectangle {
      id: srow
      required property var modelData
      required property int index
      objectName: "schedule-" + index
      readonly property bool bad: /^(failed|held|could not)/.test(modelData.status || "")
      Layout.fillWidth: true
      implicitHeight: scheduleContent.implicitHeight + 20
      radius: 6
      color: bad ? Qt.tint(theme.surface, Qt.alpha(theme.urgent, 0.08)) : theme.surface
      opacity: modelData.paused ? 0.7 : 1
      Stripe { shown: srow.bad; color: theme.urgent }

      RowLayout {
        id: scheduleContent
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
        spacing: 14
        Label { Layout.alignment: Qt.AlignTop; text: "󰃰"; color: srow.modelData.paused ? theme.muted : theme.accent }
        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3
          Label { Layout.fillWidth: true; text: srow.modelData.title; color: theme.foreground; elide: Text.ElideRight }
          Label {
            Layout.fillWidth: true
            text: [srow.modelData.whenText, srow.modelData.paused ? "paused" : "next " + srow.modelData.nextText,
                   srow.modelData.place].filter(Boolean).join("   ·   ")
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
          }
        }
        Label {
          objectName: "schedule-status-" + srow.index
          Layout.alignment: Qt.AlignTop
          visible: !!srow.modelData.lastText
          text: (srow.modelData.status || "ran") + " · " + srow.modelData.lastText
          color: srow.bad ? theme.urgent : srow.modelData.status === "running" ? theme.accent : theme.muted
          elide: Text.ElideRight
          Layout.maximumWidth: 260
        }
        Row {
          Layout.alignment: Qt.AlignTop
          spacing: 12
          IconButton {
            objectName: "schedule-run-" + srow.index
            glyph: "󰑮"
            tip: "Queue it now, whatever the time"
            onActivated: page.act(schedules.runNow(srow.modelData.id))
          }
          IconButton {
            objectName: "schedule-pause-" + srow.index
            glyph: srow.modelData.paused ? "󰐊" : "󰏤"
            tip: srow.modelData.paused ? "Resume: run on its times again" : "Pause: skip its times until resumed"
            onActivated: page.act(schedules.setPaused(srow.modelData.id, !srow.modelData.paused))
          }
          IconButton {
            objectName: "schedule-remove-" + srow.index
            glyph: "󰅖"
            tip: "Delete the schedule (what it already queued stays queued)"
            onActivated: page.act(schedules.remove(srow.modelData.id))
          }
        }
      }
    }
  }
  }
  Heading { visible: schedules.rows.length > 0; text: "Queued"; Layout.topMargin: 6 }

  EmptyState {
    visible: queue.tasks.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 40
    glyph: "󰒲"
    text: "No queued tasks. Use Add to queue or Schedule… on the New task page, or `omaorchestra queue add`."
  }

  ListView {
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: 6
    model: queue.rows
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Rectangle {
      id: row
      required property var modelData
      required property int index
      objectName: modelData.fleetRow ? "queued-fleet-" + modelData.fleet : "queued-" + index
      readonly property int taskIndex: modelData.index
      width: ListView.view.width
      height: (modelData.fleetRow ? fleetContent.implicitHeight : rowContent.implicitHeight) + 20
      radius: 6
      readonly property bool chained: !!(modelData.after || modelData.parent_session)
      readonly property bool waiting: modelData.state === "waiting"
      readonly property bool bad: modelData.state === "failed" || modelData.state === "held"
      color: bad ? Qt.tint(theme.surface, Qt.alpha(theme.urgent, 0.08)) : theme.surface
      Stripe { shown: parent.bad; color: theme.urgent }
      opacity: modelData.state === "paused" || waiting ? 0.7 : 1

      // A fleet run's nodes waiting to start: one row, opening the run.
      RowLayout {
        id: fleetContent
        visible: !!row.modelData.fleetRow
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
        spacing: 14
        Label { text: "󰡉"; color: theme.accent }
        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3
          Label { Layout.fillWidth: true; text: "omafleet: " + (row.modelData.fleet || ""); color: theme.foreground; elide: Text.ElideRight }
          Label {
            Layout.fillWidth: true
            text: (row.modelData.nodes || []).length + " to go: " + (row.modelData.nodes || []).join(", ") + "   ·   " + (row.modelData.place || "")
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
          }
        }
        AppButton { objectName: "queued-open-fleet"; text: "Open run"; onClicked: page.openFleet(row.modelData.fleet) }
      }

      RowLayout {
        id: rowContent
        visible: !row.modelData.fleetRow
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14
                  leftMargin: row.chained ? 40 : 14 }
        spacing: 14

        Label {
          Layout.alignment: Qt.AlignTop
          text: row.chained ? "↳" : (row.taskIndex + 1) + "."
          color: theme.muted
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3
          Label { Layout.fillWidth: true; text: (row.modelData.base_task || row.modelData.task || "").split("\n")[0]; color: theme.foreground; elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
          Label {
            Layout.fillWidth: true
            text: [row.modelData.step ? "step " + row.modelData.step + (row.modelData.recipe ? " of " + row.modelData.recipe : "") : "",
                   row.modelData.review ? "review" : "",
                   row.modelData.place, row.modelData.model || "",
                   row.modelData.same_worktree ? "same worktree"
                   : row.modelData.worktree === false ? "no worktree" : ""].filter(Boolean).join("   ·   ")
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
          }
          Label {
            visible: !!row.modelData.error
            Layout.fillWidth: true
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            text: (row.modelData.state === "held" ? "Held: " : "Failed to start: ") + (row.modelData.error || "")
            color: theme.urgent
            font.pixelSize: 12
          }
        }

        Label {
          Layout.alignment: Qt.AlignTop
          text: row.modelData.state === "pending" ? (queue.held ? "held" : queue.blockedText ? "usage limit" : "waiting")
                : row.waiting ? "after step " + ((row.modelData.step || 2) - 1)
                : row.modelData.state
          color: row.modelData.state === "failed" || row.modelData.state === "held" ? theme.urgent : theme.muted
        }

        Row {
          Layout.alignment: Qt.AlignTop
          spacing: 12
          IconButton { glyph: "󰁝"; tip: "Move up"; visible: row.taskIndex > 0; onActivated: page.act(queue.move(row.modelData.id, row.taskIndex - 1)) }
          IconButton { glyph: "󰁅"; tip: "Move down"; visible: row.taskIndex < queue.tasks.length - 1; onActivated: page.act(queue.move(row.modelData.id, row.taskIndex + 1)) }
          IconButton {
            objectName: "queued-pause-" + row.index
            visible: !row.waiting
            glyph: row.modelData.state === "pending" ? "󰏤" : "󰐊"
            tip: row.modelData.state === "pending" ? "Pause" : row.modelData.state === "failed" ? "Retry" : "Resume"
            onActivated: page.act(queue.setPaused(row.modelData.id, row.modelData.state === "pending"))
          }
          IconButton { objectName: "queued-run-" + row.index; visible: !row.waiting; glyph: "󰑮"; tip: "Start now, whatever the limit"; onActivated: page.act(queue.runNow(row.modelData.id)) }
          IconButton { objectName: "queued-cancel-" + row.index; glyph: "󰅖"; tip: "Remove from the queue"; onActivated: page.act(queue.cancel(row.modelData.id)) }
        }
      }
    }
  }
}
