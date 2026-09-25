import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Ended sessions, newest first and grouped by day: search and filter them,
// open one to see what it did, and resume it. `sessionHistory` and `theme`
// come from Python.
ColumnLayout {
  id: page
  property string outcomeFilter: ""
  // The record shown in detail, or "" for the list.
  property string selectedKey: ""
  property var changesResult: null
  property bool changesLoading: false
  property string actionError: ""

  spacing: 16

  onVisibleChanged: if (visible) sessionHistory.reload()
  onOutcomeFilterChanged: sessionHistory.setFilter(outcomeFilter, search.text)
  onSelectedKeyChanged: {
    changesResult = null
    actionError = ""
    tabs.currentIndex = 0
  }

  readonly property var r: { sessionHistory.rows; return selectedKey ? sessionHistory.record(selectedKey) : ({}) }

  function outcomeColor(outcome) {
    return outcome === "crashed" || outcome === "never-started" ? theme.urgent
         : outcome === "finished" ? theme.accent : theme.muted
  }

  Connections {
    target: sessionHistory
    function onChangesReady(key, result) {
      if (key !== page.selectedKey) return
      page.changesResult = result
      page.changesLoading = false
    }
  }

  Shortcut { sequence: "Escape"; enabled: page.visible && page.selectedKey !== ""; onActivated: page.selectedKey = "" }

  // ---------------------------------------------------------- Detail
  ColumnLayout {
    objectName: "history-detail"
    visible: page.selectedKey !== ""
    Layout.fillWidth: true
    Layout.fillHeight: true
    spacing: 12

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      IconButton { glyph: "󰁍"; tip: "Back to the history (Esc)"; onActivated: page.selectedKey = "" }
      Rectangle { width: 10; height: 10; radius: 5; color: page.outcomeColor(page.r.outcome) }
      Label { text: page.r.project || ""; color: theme.foreground; font.pixelSize: 18; font.bold: true }
      Label {
        Layout.fillWidth: true
        text: page.r.text || ""
        color: theme.muted
        elide: Text.ElideRight
      }
      Button {
        objectName: "history-resume"
        text: "Resume"
        onClicked: page.actionError = sessionHistory.resume(page.selectedKey)
        ToolTip.visible: hovered
        ToolTip.delay: 500
        ToolTip.text: "Reopen this conversation in its folder, in a new terminal"
      }
    }

    GridLayout {
      columns: 2
      columnSpacing: 16
      rowSpacing: 4
      Layout.fillWidth: true

      Repeater {
        model: [
          ["Outcome", (page.r.outcomeLabel || "") + (page.r.reason ? "  (" + page.r.reason + ")" : "")],
          ["When", (page.r.day || "") + ", ended " + (page.r.time || "") + " after " + (page.r.length || "")],
          ["Time", sessionHistory.timeSplit(page.selectedKey).map(t => t.text + " " + t.status.toLowerCase()).join(", ")],
          ["Folder", page.r.place || ""],
          ["Agent", [page.r.agent, page.r.modelName].filter(x => !!x).join(" · ")],
          ["Branch", page.r.branch || ""],
          ["Cost", page.r.costText ? page.r.costText + (page.r.cost && !page.r.cost.real ? " (what it would cost on the API)" : "") : ""],
          ["Commits", page.r.commitsText || ""],
          ["Waited", page.r.waits ? page.r.waits + " time" + (page.r.waits === 1 ? "" : "s") + " for you" : ""],
          ["Resumed", page.r.resumed_from ? "from an earlier session" : ""]
        ].filter(row => !!row[1])

        delegate: RowLayout {
          required property var modelData
          Layout.columnSpan: 2
          spacing: 16
          Label { Layout.preferredWidth: 80; text: modelData[0]; color: theme.muted }
          Label { Layout.fillWidth: true; text: modelData[1]; color: theme.foreground; wrapMode: Text.Wrap }
        }
      }
    }

    Label {
      visible: page.actionError !== ""
      Layout.fillWidth: true
      wrapMode: Text.Wrap
      color: theme.urgent
      text: page.actionError
    }

    TabBar {
      id: tabs
      Layout.fillWidth: true
      background: Rectangle { color: "transparent" }
      onCurrentIndexChanged: if (currentIndex === 1 && !page.changesResult && page.selectedKey) {
        page.changesLoading = true
        sessionHistory.requestChanges(page.selectedKey)
      }

      Repeater {
        model: ["Activity", "Changes"]
        delegate: TabButton {
          required property string modelData
          required property int index
          objectName: "history-tab-" + modelData.toLowerCase()
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
      Layout.fillWidth: true
      Layout.fillHeight: true
      currentIndex: tabs.currentIndex

      ListView {
        id: activityList
        clip: true
        spacing: 8
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        model: page.selectedKey ? sessionHistory.activity(page.selectedKey) : []
        onCountChanged: positionViewAtEnd()

        delegate: RowLayout {
          required property var modelData
          width: ListView.view.width - 12
          spacing: 12
          Label {
            Layout.alignment: Qt.AlignTop
            Layout.preferredWidth: 20
            text: modelData.kind === "prompt" ? "󰍩" : modelData.kind === "tool" ? "󰒓" : "󰚩"
            color: modelData.kind === "prompt" ? theme.accent : theme.muted
          }
          Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: modelData.text
            color: modelData.kind === "tool" ? theme.muted : theme.foreground
          }
        }

        Label {
          anchors.centerIn: parent
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.Wrap
          visible: activityList.count === 0
          color: theme.muted
          text: page.r.transcript ? "The transcript is gone; history only ever pointed to it."
                                  : "No transcript for this session."
        }
      }

      ScrollView {
        clip: true
        contentWidth: availableWidth
        ChangesView {
          width: parent.width
          result: page.changesResult
          loading: page.changesLoading
        }
      }
    }
  }

  // ---------------------------------------------------------- Filters
  RowLayout {
    visible: page.selectedKey === ""
    Layout.fillWidth: true
    spacing: 8

    Repeater {
      model: [
        { label: "All", outcome: "" },
        { label: "Finished", outcome: "finished" },
        { label: "Stopped", outcome: "stopped" },
        { label: "Crashed", outcome: "crashed" },
        { label: "Never started", outcome: "never-started" }
      ]

      delegate: Button {
        required property var modelData
        objectName: "history-filter-" + (modelData.outcome || "all")
        readonly property bool selected: page.outcomeFilter === modelData.outcome
        readonly property int count: { sessionHistory.counts; return sessionHistory.counts[modelData.outcome] || 0 }
        text: modelData.label + "  " + count
        flat: true
        onClicked: page.outcomeFilter = modelData.outcome
        contentItem: Label {
          text: parent.text
          color: parent.selected ? theme.foreground
                 : (modelData.outcome === "crashed" || modelData.outcome === "never-started") && parent.count > 0
                   ? theme.urgent : theme.muted
          horizontalAlignment: Text.AlignHCenter
        }
        background: Rectangle {
          radius: 4
          color: parent.selected ? theme.selection : parent.hovered ? Qt.alpha(theme.selection, 0.5) : "transparent"
          border.color: theme.selection
        }
      }
    }

    TextField {
      id: search
      objectName: "history-search"
      Layout.fillWidth: true
      Layout.leftMargin: 8
      placeholderText: "Search tasks, folders, models, branches"
      placeholderTextColor: theme.muted
      color: theme.foreground
      selectionColor: theme.selection
      onTextChanged: sessionHistory.setFilter(page.outcomeFilter, text)
      background: Rectangle { radius: 4; color: theme.surface; border.color: search.activeFocus ? theme.accent : theme.selection }
    }
  }

  // ---------------------------------------------------------- Empty
  Label {
    visible: page.selectedKey === "" && sessionHistory.rows.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 24
    horizontalAlignment: Text.AlignHCenter
    wrapMode: Text.Wrap
    color: theme.muted
    text: sessionHistory.total === 0
      ? "No history yet. Each session is recorded here when it ends."
      : "No sessions match."
  }

  // ---------------------------------------------------------- List
  ListView {
    objectName: "history-list"
    visible: page.selectedKey === "" && sessionHistory.rows.length > 0
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: 6
    model: sessionHistory.rows
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    section.property: "day"
    section.delegate: Label {
      required property string section
      width: ListView.view.width
      topPadding: 10
      bottomPadding: 2
      text: section
      color: theme.muted
      font.bold: true
    }

    delegate: Rectangle {
      id: row
      required property var modelData
      objectName: "history-row-" + modelData.id
      width: ListView.view.width
      height: rowContent.implicitHeight + 20
      radius: 6
      color: rowMouse.containsMouse ? theme.selection : theme.surface

      MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: page.selectedKey = row.modelData.key
      }

      RowLayout {
        id: rowContent
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 14; rightMargin: 14 }
        spacing: 14

        Rectangle {
          Layout.alignment: Qt.AlignTop
          Layout.topMargin: 6
          width: 10; height: 10; radius: 5
          color: page.outcomeColor(row.modelData.outcome)
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 2
          RowLayout {
            spacing: 10
            Label { text: row.modelData.project; color: theme.foreground; font.bold: true }
            Label { text: row.modelData.agent + (row.modelData.modelName ? " · " + row.modelData.modelName : ""); color: theme.muted }
          }
          Label {
            visible: !!row.modelData.text
            Layout.fillWidth: true
            text: row.modelData.text
            color: theme.foreground
            elide: Text.ElideRight
          }
          Label { text: row.modelData.place; color: theme.muted; font.pixelSize: 12 }
        }

        ColumnLayout {
          Layout.alignment: Qt.AlignTop
          spacing: 2
          Label {
            Layout.alignment: Qt.AlignRight
            text: row.modelData.outcomeLabel + " · " + row.modelData.time
            color: page.outcomeColor(row.modelData.outcome)
          }
          Label {
            Layout.alignment: Qt.AlignRight
            text: [row.modelData.length + " (" + row.modelData.working + " working)", row.modelData.costText,
                   row.modelData.commitsText].filter(x => !!x).join(" · ")
            color: theme.muted
            font.pixelSize: 12
          }
        }
      }
    }
  }
}
