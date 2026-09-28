import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A run's postmortem (fleet_report): time and cost per role, each slice's
// builds and send-backs, how long gates and holds waited for you, and how
// parallel the builders really ran. What the run's shape cost and caught,
// measured rather than assumed. `fleets` and `theme` come from Python.
ColumnLayout {
  id: reportView
  property string runId: ""
  property var r: ({})
  function reload() { r = runId ? fleets.report(runId) : ({}) }
  onRunIdChanged: if (visible) reload()
  onVisibleChanged: if (visible) reload()
  Connections { target: fleets; function onRunChanged(id) { if (id === reportView.runId && reportView.visible) reportView.reload() } }
  spacing: 6

  readonly property string money: r.estimated ? "~$" : "$"
  component Head: Label { color: theme.muted; font.pixelSize: 12; Layout.topMargin: 6 }
  component Cell: Label { color: theme.foreground; elide: Text.ElideRight }

  Label {
    objectName: "report-summary"
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: theme.foreground
    text: r.run ? "Over " + r.spanText + (r.outside ? "" : ", " + reportView.money + r.cost.toFixed(2))
                  + ". Sent back " + r.sent_back + " time" + (r.sent_back === 1 ? "" : "s") + " (" + r.rejects + " REJECT)"
                  + (r.retries ? ", " + r.retries + " retr" + (r.retries === 1 ? "y" : "ies") : "")
                  + (r.stalls ? ", " + r.stalls + " stall" + (r.stalls === 1 ? "" : "s") : "") + "." : ""
  }
  Label {
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: theme.muted
    visible: !!r.run
    text: r.run ? "Builders: at most " + r.parallel.builders_at_once + " at once, together "
                  + Math.round(r.parallel.builders_together * 100) + "% of their time; " + r.parallel.busy
                  + " agents busy on average."
                  + r.gates.map(g => " The " + g.gate + " gate waited " + g.text + " for you.").join("")
                  + (r.holds.count ? " Held " + r.holds.count + " time" + (r.holds.count === 1 ? "" : "s") + ", " + r.heldText + " in all." : "") : ""
  }

  Head { text: "By role" }
  RowLayout {
    spacing: 12
    Repeater {
      model: ["Role", "Agents", "Working", "Waiting for you", reportView.r.outside ? "" : "Cost"]
      delegate: Label { required property string modelData; text: modelData; color: theme.muted; font.pixelSize: 12; Layout.preferredWidth: 110 }
    }
  }
  Repeater {
    model: reportView.r.roles || []
    delegate: RowLayout {
      required property var modelData
      objectName: "report-role-" + modelData.role
      spacing: 12
      Cell { text: modelData.role; Layout.preferredWidth: 110 }
      Cell { text: String(modelData.nodes); Layout.preferredWidth: 110 }
      Cell { text: modelData.workingText; Layout.preferredWidth: 110 }
      Cell { text: modelData.waitingText; Layout.preferredWidth: 110; color: modelData.waiting_s ? theme.urgent : theme.muted }
      Cell {
        visible: !reportView.r.outside
        Layout.preferredWidth: 160
        text: reportView.money + modelData.cost.toFixed(2) + "  (" + Math.round(modelData.share * 100) + "%)"
      }
    }
  }

  Head { visible: (reportView.r.slices || []).length > 0; text: "By slice" }
  Repeater {
    model: reportView.r.slices || []
    delegate: RowLayout {
      required property var modelData
      spacing: 12
      Cell { text: modelData.slice; Layout.preferredWidth: 110 }
      Cell { text: modelData.builds + " build" + (modelData.builds === 1 ? "" : "s"); Layout.preferredWidth: 110 }
      Cell {
        text: modelData.rejects + " REJECT"
        color: modelData.rejects ? theme.urgent : theme.muted
        Layout.preferredWidth: 110
      }
      Cell { text: modelData.verdict || "-"; Layout.preferredWidth: 110 }
      Cell {
        visible: !reportView.r.outside
        text: reportView.money + modelData.cost.toFixed(2) + (modelData.extra_files ? "   " + modelData.extra_files + " files outside it" : "")
        Layout.preferredWidth: 220
      }
    }
  }
}
