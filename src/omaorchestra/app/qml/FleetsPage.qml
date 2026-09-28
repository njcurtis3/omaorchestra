import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The omafleet tab: fleet runs on the left (Needs you, Running, Finished),
// the picked run on the right; Roles and Fleets as views of their own; and
// the New fleet run form. The run list is a keyed model, so a picked run
// stays picked as runs change. `fleets`, `sessions` and `theme` come from
// Python; `now` ticks from the window.
ColumnLayout {
  id: page
  property real now: Date.now() / 1000
  property string view: "runs"  // runs, roles, fleets
  property string selectedId: ""
  property bool creating: false
  signal openSession(string sessionId)
  signal openHistory(string sessionId)
  spacing: 12

  function show(runId) {
    view = "runs"
    creating = false
    const match = fleets.runs.count ? Array.from({ length: fleets.runs.count }, (_, i) => fleets.runs.get(i))
                                          .find(r => r.id.startsWith(runId)) : null
    selectedId = match ? match.id : runId
  }
  function newRun(goal, folder) {
    view = "runs"
    creating = true
    form.prefill(goal, folder)
  }
  onVisibleChanged: if (visible && sessions.connected) fleets.refresh()
  // Outside runs (graph_agents, agent teams) are files other programs
  // write: read again every few seconds while the tab is shown.
  Timer { interval: 8000; repeat: true; running: page.visible; onTriggered: fleets.refreshOutside() }

  RowLayout {
    Layout.fillWidth: true
    spacing: 6
    Repeater {
      model: [{ id: "runs", label: "Runs" }, { id: "roles", label: "Roles" }, { id: "fleets", label: "Fleets" }]
      delegate: Button {
        required property var modelData
        objectName: "fleets-view-" + modelData.id
        text: modelData.label + (modelData.id === "runs" && fleets.needing ? "  " + fleets.needing + "!" : "")
        flat: true
        onClicked: { page.view = modelData.id; page.creating = false }
        contentItem: Label {
          text: parent.text
          color: page.view === modelData.id ? theme.foreground : modelData.id === "runs" && fleets.needing ? theme.urgent : theme.muted
          horizontalAlignment: Text.AlignHCenter
        }
        background: Rectangle { radius: 4; implicitWidth: 90; color: page.view === modelData.id ? theme.selection : "transparent"; border.color: theme.selection }
      }
    }
    Item { Layout.fillWidth: true }
    FleetButton {
      objectName: "fleets-new"
      primary: true
      text: "New fleet run"
      onClicked: page.newRun("", "")
    }
  }

  // ---------------------------------------------------------- runs
  RowLayout {
    visible: page.view === "runs"
    Layout.fillWidth: true
    Layout.fillHeight: true
    spacing: 16

    ListView {
      id: runList
      objectName: "fleet-runs"
      visible: fleets.runs.count > 0
      Layout.preferredWidth: 300
      Layout.fillHeight: true
      clip: true
      spacing: 6
      model: fleets.runs
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar {}
      section.property: "group"
      section.delegate: Label {
        required property string section
        objectName: "section-" + section
        text: section
        color: section === "Needs you" ? theme.urgent : theme.muted
        font.pixelSize: 12
        topPadding: 8
        bottomPadding: 2
      }

      delegate: Rectangle {
        id: runRow
        required property var item
        required property string key
        objectName: "fleet-run-" + key
        width: ListView.view.width
        height: rowBody.implicitHeight + 18
        radius: 6
        color: page.selectedId === key && !page.creating ? theme.selection : runMouse.containsMouse ? Qt.alpha(theme.selection, 0.5) : theme.surface
        border.color: item.group === "needs-you" ? theme.urgent : "transparent"
        opacity: item.outside ? 0.85 : 1

        MouseArea {
          id: runMouse
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: { page.creating = false; page.selectedId = runRow.key }
        }

        ColumnLayout {
          id: rowBody
          anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
          spacing: 4
          Label { Layout.fillWidth: true; text: runRow.item.goal; color: theme.foreground; font.bold: true; elide: Text.ElideRight }
          Label {
            Layout.fillWidth: true
            text: runRow.item.statusText + (runRow.item.status === "held" && runRow.item.reason ? ": " + runRow.item.reason : "")
            color: runRow.item.group === "needs-you" ? theme.urgent : runRow.item.status === "running" ? theme.accent : theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
          }
          Flow {
            Layout.fillWidth: true
            spacing: 4
            Repeater {
              model: runRow.item.dots
              delegate: FleetDot { required property var modelData; state_: modelData.state }
            }
          }
          Label {
            Layout.fillWidth: true
            text: [runRow.item.project, runRow.item.shape, sessions.duration(runRow.item.created, page.now),
                   "$" + runRow.item.spent.toFixed(2)].filter(Boolean).join("  ·  ")
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideRight
          }
        }
      }
    }

    // What the right side shows: the form, a run, or how to begin.
    ScrollView {
      id: formScroll
      visible: page.creating
      Layout.fillWidth: true
      Layout.fillHeight: true
      clip: true
      contentWidth: availableWidth
      NewFleetForm {
        id: form
        width: formScroll.availableWidth
        onStarted: id => { page.creating = false; page.selectedId = id }
        onCancelled: page.creating = false
      }
    }

    FleetRunView {
      objectName: "run-view"
      visible: !page.creating && page.selectedId !== "" && fleets.runs.count > 0
      Layout.fillWidth: true
      Layout.fillHeight: true
      runId: page.selectedId
      now: page.now
      onOpenSession: id => page.openSession(id)
      onOpenHistory: id => page.openHistory(id)
    }

    ColumnLayout {
      objectName: "fleets-empty"
      visible: !page.creating && (fleets.runs.count === 0 || page.selectedId === "")
      Layout.fillWidth: true
      Layout.fillHeight: true
      spacing: 10
      Item { Layout.preferredHeight: 24 }
      Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: theme.foreground
        font.pixelSize: 16
        text: fleets.runs.count === 0 ? "No fleet runs yet." : "Pick a run on the left."
      }
      Label {
        visible: fleets.runs.count === 0
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: theme.muted
        text: "A fleet run puts a scout, an architect, builders and reviewers on one goal. You approve the plan before anything is built, "
              + "and a reviewer who never saw the code written checks each part.\n\n"
              + "Worth it for work of several parts, or work you would not merge unreviewed. For one file or one bug, "
              + "a single task (New task) is cheaper and as good."
      }
      FleetButton {
        visible: fleets.runs.count === 0
        primary: true
        text: "New fleet run"
        onClicked: page.newRun("", "")
      }
      Item { Layout.fillHeight: true }
    }
  }

  // ---------------------------------------------------------- roles and fleets
  ScrollView {
    id: rolesScroll
    visible: page.view === "roles"
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    contentWidth: availableWidth
    FleetRoles {
      width: rolesScroll.availableWidth
      folder: page.selectedId ? fleets.state(page.selectedId).folder || "" : ""
    }
  }
  ScrollView {
    id: templatesScroll
    visible: page.view === "fleets"
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    contentWidth: availableWidth
    FleetTemplates { width: templatesScroll.availableWidth }
  }
}
