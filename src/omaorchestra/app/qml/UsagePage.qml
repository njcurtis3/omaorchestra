import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Spend and limits: provider spend today against the budget, the
// subscription's limits (from Omarchy), what each session costs, and
// provider balances. `spend` and `theme` come from Python.
ScrollView {
  id: page
  clip: true
  contentWidth: availableWidth

  onVisibleChanged: if (visible) { spend.refresh(); sessionHistory.reload() }
  readonly property var r: spend.report
  property int statsDays: 30
  readonly property var h: { sessionHistory.rows; return sessionHistory.stats(statsDays) }

  component Section: Label { color: theme.foreground; font.bold: true; font.pixelSize: 16; topPadding: 8 }
  component Line: Label { color: theme.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }

  ColumnLayout {
    width: page.availableWidth
    spacing: 6

    RowLayout {
      Layout.fillWidth: true
      Label {
        Layout.fillWidth: true
        color: theme.muted
        wrapMode: Text.Wrap
        text: "Spend through API providers is real; a subscription session's cost is what the same work would cost on the API."
      }
      IconButton { glyph: spend.loading ? "󰔟" : "󰑐"; tip: "Refresh"; onActivated: spend.refresh() }
    }

    Section { text: "Today" + (page.r.day ? " (" + page.r.day + ")" : "") }
    Line {
      objectName: "usage-today"
      color: page.r.budget && page.r.spent >= page.r.budget ? theme.urgent : theme.foreground
      text: page.r.spent === undefined ? "…"
        : "Provider spend: $" + page.r.spent.toFixed(2)
          + (page.r.budget ? " of the $" + page.r.budget + " daily budget" : " (no daily budget; set one in Settings)")
    }
    Repeater {
      model: page.r.byProvider ? Object.keys(page.r.byProvider) : []
      delegate: Line { required property string modelData; text: "   " + modelData + ": $" + page.r.byProvider[modelData].toFixed(2) }
    }

    Section { text: "Subscription" }
    Line { visible: !page.r.limits || page.r.limits.length === 0; text: "No usage record from Omarchy's agents widget." }
    Repeater {
      model: page.r.limits || []
      delegate: RowLayout {
        required property var modelData
        Layout.fillWidth: true
        spacing: 12
        Label { Layout.preferredWidth: 220; text: modelData.label; color: theme.foreground }
        Rectangle {
          Layout.preferredWidth: 220
          height: 8
          radius: 4
          color: theme.selection
          Rectangle {
            width: parent.width * Math.min(1, modelData.percent)
            height: parent.height
            radius: 4
            color: modelData.percent >= 0.9 ? theme.urgent : theme.accent
          }
        }
        Label {
          text: Math.round(modelData.percent * 100) + "%" + (modelData.resetsAt ? ", resets " + spend.resetText(modelData.resetsAt) : "")
          color: modelData.percent >= 0.9 ? theme.urgent : theme.muted
        }
      }
    }

    Section { text: "Sessions" }
    Line { visible: !page.r.sessions || page.r.sessions.length === 0; text: "No sessions." }
    Repeater {
      model: page.r.sessions || []
      delegate: RowLayout {
        required property var modelData
        Layout.fillWidth: true
        spacing: 12
        Label { Layout.preferredWidth: 200; text: modelData.project; color: theme.foreground; elide: Text.ElideRight }
        Label { Layout.preferredWidth: 110; text: modelData.model; color: theme.muted }
        Label {
          Layout.preferredWidth: 90
          horizontalAlignment: Text.AlignRight
          text: "$" + modelData.usd.toFixed(2)
          color: modelData.real ? theme.foreground : theme.muted
        }
        Label {
          Layout.fillWidth: true
          color: theme.muted
          text: (modelData.real ? "spent via " + modelData.provider : "API-equivalent")
            + (modelData.unpriced.length ? "  (no price for " + modelData.unpriced.join(", ") + ")" : "")
        }
      }
    }

    // ---------------------------------------------------------- History
    RowLayout {
      Layout.fillWidth: true
      Layout.topMargin: 8
      Section { text: "History"; topPadding: 0 }
      Item { Layout.fillWidth: true }
      Repeater {
        model: [{ days: 7, label: "7 days" }, { days: 30, label: "30 days" }, { days: 0, label: "All" }]
        delegate: Button {
          required property var modelData
          objectName: "stats-" + modelData.days
          readonly property bool selected: page.statsDays === modelData.days
          text: modelData.label
          flat: true
          onClicked: page.statsDays = modelData.days
          contentItem: Label { text: parent.text; color: parent.selected ? theme.foreground : theme.muted; font.pixelSize: 12 }
          background: Rectangle {
            radius: 4
            color: parent.selected ? theme.selection : parent.hovered ? Qt.alpha(theme.selection, 0.5) : "transparent"
            border.color: theme.selection
          }
        }
      }
    }
    Line {
      objectName: "stats-summary"
      text: page.h.sessions === 0 ? "No ended sessions in this time. Each session is recorded when it ends (History page)."
        : page.h.sessions + " session" + (page.h.sessions === 1 ? "" : "s") + ": "
          + Object.keys(page.h.outcomes).filter(o => page.h.outcomes[o]).map(o => page.h.outcomes[o] + " " + o).join(", ")
    }
    Line {
      visible: page.h.sessions > 0
      text: "Waited for you " + page.h.waits.total + " time" + (page.h.waits.total === 1 ? "" : "s")
        + " (" + page.h.waits.per_session + " per session)"
        + (page.h.waits.medianText ? "; you answered in " + page.h.waits.medianText + " (median), "
           + page.h.waits.longestText + " at most" : "")
    }
    Repeater {
      model: page.h.sessions > 0 ? [["By project", page.h.by_project], ["By agent", page.h.by_agent],
                                    ["By model", page.h.by_model]] : []
      delegate: ColumnLayout {
        required property var modelData
        Layout.fillWidth: true
        Layout.topMargin: 6
        spacing: 2
        Label { text: modelData[0]; color: theme.muted; font.pixelSize: 12 }
        Repeater {
          model: modelData[1].slice(0, 8)
          delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: 12
            Label { Layout.preferredWidth: 200; text: modelData.name; color: theme.foreground; elide: Text.ElideRight }
            Label { Layout.preferredWidth: 90; text: modelData.sessions + " session" + (modelData.sessions === 1 ? "" : "s"); color: theme.muted }
            Label { Layout.preferredWidth: 150; text: modelData.workingText + " working"; color: theme.muted }
            Label { Layout.fillWidth: true; text: modelData.costText; color: theme.muted }
          }
        }
      }
    }

    Section { visible: !!(page.r.balances && page.r.balances.length); text: "Provider balances" }
    Repeater {
      model: page.r.balances || []
      delegate: Line {
        required property var modelData
        color: modelData.error ? theme.urgent : theme.muted
        text: modelData.provider + ": " + (modelData.error ? modelData.error
          : (modelData.usage_daily || 0).toFixed(2) + " credits used today, " + (modelData.usage || 0).toFixed(2) + " in all"
            + (modelData.limit ? ", " + modelData.limit_remaining.toFixed(2) + " of " + modelData.limit.toFixed(2) + " left" : ""))
      }
    }
  }
}
