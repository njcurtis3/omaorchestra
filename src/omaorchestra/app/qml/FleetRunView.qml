import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One fleet run: its header (goal, folder, branch, shape, spend against its
// budget, and what can be done), the decisions it waits on you for, then
// the run as a graph, a board or a timeline, and the picked node's detail.
// Below a set width it is the board only. `fleets`, `sessions` and `theme`
// come from Python.
ScrollView {
  id: view
  property string runId: ""
  property real now: Date.now() / 1000
  signal openSession(string sessionId)
  signal openHistory(string sessionId)

  property string mode: "graph"  // graph, board, timeline, report
  property string nodeId: ""
  property string message: ""
  property bool messageBad: false
  readonly property bool narrow: availableWidth < 480
  readonly property string shownMode: narrow && mode === "graph" ? "board" : mode

  // Re-read whenever a run changes (fleets.revision goes up).
  readonly property var r: { fleets.revision; return runId ? fleets.row(runId) : ({}) }
  readonly property var cardList: { fleets.revision; return runId ? fleets.cards(runId) : [] }
  onRunIdChanged: { nodeId = ""; message = "" }

  function say(text, bad) { message = text; messageBad = bad }
  property bool cancelArmed: false
  Timer { id: disarm; interval: 4000; onTriggered: view.cancelArmed = false }

  clip: true
  contentWidth: availableWidth

  ColumnLayout {
    width: view.availableWidth
    spacing: 12

    // ------------------------------------------------------ header
    ColumnLayout {
      objectName: "run-header"
      Layout.fillWidth: true
      spacing: 4
      Label {
        Layout.fillWidth: true
        text: view.r.goal || ""
        color: theme.foreground
        font.pixelSize: 18
        font.bold: true
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      }
      Label {
        objectName: "run-status"
        Layout.fillWidth: true
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        text: (view.r.statusText || "") + (view.r.reason && view.r.status === "held" ? ": " + view.r.reason : "")
        color: view.r.group === "needs-you" ? theme.urgent : view.r.status === "running" ? theme.accent : theme.muted
      }
      Label {
        Layout.fillWidth: true
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere  // a long branch breaks rather than overflows
        color: theme.muted
        font.pixelSize: 12
        text: [view.r.place, view.r.fleet ? view.r.fleet + " fleet" : "", view.r.shape, view.r.branch ? " " + view.r.branch : "",
               view.r.elapsed].filter(Boolean).join("   ·   ")
      }
      // Spend against the budget, then what can be done: on one line, or
      // two when the view is narrow.
      GridLayout {
        Layout.fillWidth: true
        columns: view.availableWidth >= 560 ? 2 : 1
        columnSpacing: 8
        rowSpacing: 8
      RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Label {
          visible: !view.r.outside
          text: "$" + (view.r.spent || 0).toFixed(2) + (view.r.budget ? " of $" + view.r.budget : " spent (no budget)")
          color: theme.muted
          font.pixelSize: 12
        }
        Rectangle {
          visible: !!view.r.budget
          Layout.fillWidth: true
          Layout.preferredWidth: 160
          Layout.maximumWidth: 160
          height: 6
          radius: 3
          color: theme.surface
          Rectangle {
            width: parent.width * Math.min(1, (view.r.spent || 0) / Math.max(0.01, view.r.budget || 1))
            height: parent.height
            radius: 3
            color: (view.r.spent || 0) >= (view.r.budget || 0) ? theme.urgent : theme.accent
          }
        }
        Item { Layout.fillWidth: true }
      }
      RowLayout {
        Layout.alignment: Qt.AlignRight
        spacing: 8
        IconButton { glyph: "󰉋"; tip: "Open its folder"; visible: !!view.r.place; onActivated: sessions.openFolder(view.r.folder) }
        IconButton { glyph: "󰆏"; tip: "Copy its branch"; visible: !!view.r.branch; onActivated: sessions.copyPath(view.r.branch) }
        AppButton {
          objectName: "run-pause"
          visible: !view.r.outside && (view.r.status === "running" || (view.r.status === "held" && view.r.heldBy === "paused"))
          text: view.r.status === "running" ? "Pause" : "Resume"
          onClicked: {
            const result = fleets.pause(view.runId, view.r.status === "running")
            view.say(result.error || result.message, !!result.error)
          }
        }
        AppButton {
          objectName: "run-cancel"
          visible: !view.r.outside && view.r.status === "running"
          danger: true
          text: view.cancelArmed ? "Really cancel?" : "Cancel run"
          onClicked: {
            if (!view.cancelArmed) { view.cancelArmed = true; disarm.restart(); return }
            view.cancelArmed = false
            const result = fleets.cancel(view.runId)
            view.say(result.error || result.message, !!result.error)
          }
        }
      }
      }
    }

    // Someone else's run: watched, never answered from here.
    Rectangle {
      objectName: "run-outside"
      visible: !!view.r.outside
      Layout.fillWidth: true
      implicitHeight: outsideText.implicitHeight + 16
      radius: 6
      color: theme.surface
      border.color: theme.selection
      Label {
        id: outsideText
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        color: theme.muted
        text: "Read-only: " + (view.r.outsideLabel || "an outside run") + ", from " + (view.r.source || "") + ". omaorchestra only watches it; "
              + (view.r.outside === "graph_agents" ? "answer its gates where it runs (its orchestrator's session)." : "talk to it in its lead's session.")
      }
    }

    Label {
      objectName: "run-message"
      visible: !!view.message
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      text: view.message
      color: view.messageBad ? theme.urgent : theme.accent
    }

    // ------------------------------------------------------ decisions
    Repeater {
      model: view.cardList
      delegate: FleetCard {
        required property var modelData
        Layout.fillWidth: true
        card_: modelData
        runId: view.runId
        now: view.now
        onSaid: (text, bad) => view.say(text, bad)
        onOpenSession: id => view.openSession(id)
      }
    }

    // ------------------------------------------------------ graph, board, timeline
    FleetTeam {
      objectName: "run-team"
      visible: view.r.outside === "agent-team"
      Layout.fillWidth: true
      runId: view.r.outside === "agent-team" ? view.runId : ""
    }

    Flow {
      visible: view.r.outside !== "agent-team"
      Layout.fillWidth: true
      spacing: 6
      Repeater {
        model: [{ id: "graph", label: "Graph" }, { id: "board", label: "Board" }, { id: "timeline", label: "Timeline" },
                { id: "report", label: "Report" }]
        delegate: Chip {
          required property var modelData
          objectName: "run-view-" + modelData.id
          visible: !(view.narrow && modelData.id === "graph")
          text: modelData.label
          selected: view.shownMode === modelData.id
          onClicked: view.mode = modelData.id
        }
      }
      Label {
        visible: view.narrow
        height: 32
        verticalAlignment: Text.AlignVCenter
        leftPadding: 6
        text: "(the graph needs a wider window)"
        color: theme.muted
        font.pixelSize: 12
      }
    }

    FleetGraph {
      objectName: "run-graph"
      visible: view.shownMode === "graph" && view.r.outside !== "agent-team"
      Layout.fillWidth: true
      runId: view.runId
      chosen: view.nodeId
      onPick: key => {
        const box = graph.nodes.find(n => n.key === key)
        view.nodeId = box && box.node ? box.node : ""
      }
      readonly property var graph: layout
    }

    FleetBoard {
      objectName: "run-board"
      visible: view.shownMode === "board" && view.r.outside !== "agent-team"
      Layout.fillWidth: true
      runId: view.runId
      chosen: view.nodeId.split(".")[1] || ""
      onPick: sliceId => {
        const g = fleets.graph(view.runId)
        const box = g.nodes.find(n => n.key === "builder." + sliceId)
        view.nodeId = box && box.node ? box.node : ""
      }
    }

    FleetTimeline {
      objectName: "run-timeline"
      visible: view.shownMode === "timeline" && view.r.outside !== "agent-team"
      Layout.fillWidth: true
      runId: view.runId
    }

    FleetReport {
      objectName: "run-report"
      visible: view.shownMode === "report" && view.r.outside !== "agent-team"
      Layout.fillWidth: true
      runId: view.runId
    }

    FleetNode {
      visible: view.nodeId !== ""
      Layout.fillWidth: true
      runId: view.runId
      nodeId: view.nodeId
      onClosed: view.nodeId = ""
      onOpenSession: id => view.openSession(id)
      onOpenHistory: id => view.openHistory(id)
    }

    Item { Layout.preferredHeight: 12 }
  }
}
