import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The sessions dashboard: filters, then every session as a list or a grid,
// waiting first. Click a session for its details; the jump icon focuses its terminal. `sessions` and
// `theme` come from Python; `now` ticks from the window.
ColumnLayout {
  id: page
  property real now: Date.now() / 1000
  property string statusFilter: ""
  property bool hideFleet: false
  // The width to lay out for (the window's pageWidth; see Main.qml).
  readonly property real room: ApplicationWindow.window ? ApplicationWindow.window.pageWidth : width
  signal openFleet(string runId)
  property bool grid: false
  // The session shown in detail, or "" for the list.
  property string selectedId: ""

  spacing: 16

  // Re-read whenever the sessions change (sessions.rows notifies) or a filter does.
  readonly property var shown: {
    sessions.rows
    const rows = sessions.filtered(statusFilter, search.text)
    return hideFleet ? rows.filter(r => !r.fleet) : rows
  }
  readonly property int fleetCount: { sessions.rows; return sessions.rows.filter(r => !!r.fleet).length }

  function statusColor(status) {
    return status === "needs-input" ? theme.urgent : status === "working" ? theme.accent : theme.muted
  }

  SessionDetail {
    objectName: "detail"
    visible: page.selectedId !== ""
    Layout.fillWidth: true
    Layout.fillHeight: true
    sessionId: page.selectedId
    now: page.now
    onBack: page.selectedId = ""
  }

  // ---------------------------------------------------------- Filters
  // The chips, then the search: on one line, or two in a narrow window.
  GridLayout {
    visible: page.selectedId === ""
    Layout.fillWidth: true
    columns: page.room >= 780 ? 2 : 1
    columnSpacing: 16
    rowSpacing: 8

    Flow {
      Layout.fillWidth: page.room < 780
      spacing: 8
      Repeater {
        model: [
          { label: "All", status: "", count: sessions.total },
          { label: "Waiting", status: "needs-input", count: sessions.waiting },
          { label: "Working", status: "working", count: sessions.working },
          { label: "Idle", status: "idle", count: sessions.idle }
        ]

        delegate: Chip {
          required property var modelData
          objectName: "filter-" + (modelData.status || "all")
          text: modelData.label + "  " + modelData.count
          selected: page.statusFilter === modelData.status
          urgent: modelData.status === "needs-input" && modelData.count > 0
          onClicked: page.statusFilter = modelData.status
        }
      }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 8

      TextField {
        id: search
        objectName: "search"
        Layout.fillWidth: true
        implicitHeight: 32
        placeholderText: "Filter by project, title, path, branch or model"
        placeholderTextColor: theme.muted
        color: theme.foreground
        selectionColor: theme.selection
        background: Rectangle { radius: 4; color: theme.surface; border.color: search.activeFocus ? theme.accent : theme.selection }
      }

      Chip {
        objectName: "filter-fleet"
        visible: page.fleetCount > 0
        text: (page.hideFleet ? "Show" : "Hide") + " fleet  " + page.fleetCount
        onClicked: page.hideFleet = !page.hideFleet
        ToolTip.visible: hovered
        ToolTip.delay: 500
        ToolTip.text: (page.hideFleet ? "Show" : "Hide") + " the sessions omafleet runs"
      }

      IconButton {
        glyph: page.grid ? "󰕰" : "󰕮"
        tip: page.grid ? "Show as a list" : "Show as a grid"
        onActivated: page.grid = !page.grid
      }
    }
  }

  // ---------------------------------------------------------- Empty states
  Label {
    visible: page.selectedId === "" && page.shown.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 24
    horizontalAlignment: Text.AlignHCenter
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.muted
    text: sessions.total === 0
      ? "No agent sessions. They appear here as soon as an agent starts."
      : "No sessions match."
  }

  // ---------------------------------------------------------- List
  ListView {
    visible: page.selectedId === "" && !page.grid && page.shown.length > 0
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: 6
    model: page.shown
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Rectangle {
      id: listRow
      required property var modelData
      objectName: "row-" + modelData.id
      width: ListView.view.width
      height: listContent.implicitHeight + 20
      radius: 6
      color: rowMouse.containsMouse ? theme.selection : theme.surface

      MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: page.selectedId = listRow.modelData.id
      }

      RowLayout {
        id: listContent
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 14; rightMargin: 14 }
        spacing: 14

        Rectangle {
          Layout.alignment: Qt.AlignTop
          Layout.topMargin: 6
          width: 10; height: 10; radius: 5
          color: page.statusColor(listRow.modelData.status)
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3

          // Each part shrinks (and elides) before the row grows past the window:
          // fillWidth lets a layout shrink an item, maximumWidth stops it growing.
          RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Label {
              Layout.fillWidth: true
              Layout.maximumWidth: Math.ceil(implicitWidth)
              Layout.minimumWidth: Math.min(Math.ceil(implicitWidth), 100)
              text: listRow.modelData.project
              color: theme.foreground
              font.bold: true
              elide: Text.ElideRight
            }
            Label {
              visible: !!listRow.modelData.branch
              Layout.fillWidth: true
              Layout.maximumWidth: Math.ceil(implicitWidth)
              text: " " + (listRow.modelData.branch || "")
              color: theme.muted
              elide: Text.ElideMiddle
            }
            Label {
              visible: !!listRow.modelData.modelName
              Layout.fillWidth: true
              Layout.maximumWidth: Math.ceil(implicitWidth)
              text: listRow.modelData.modelName
              color: theme.muted
              elide: Text.ElideRight
            }
            Label {
              Layout.fillWidth: true
              Layout.maximumWidth: Math.ceil(implicitWidth)
              elide: Text.ElideRight
              objectName: "fleet-chip-" + listRow.modelData.id
              visible: !!listRow.modelData.fleet
              text: "omafleet · " + (listRow.modelData.node || "").replace(/\./g, " ")
              color: theme.accent
              font.pixelSize: 12
              leftPadding: 6; rightPadding: 6; topPadding: 1; bottomPadding: 1
              background: Rectangle { radius: 8; color: "transparent"; border.color: theme.accent }
              MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.openFleet(listRow.modelData.fleet) }
            }
            Item { Layout.fillWidth: true }
          }
          Label {
            visible: !!listRow.modelData.title
            Layout.fillWidth: true
            text: listRow.modelData.title || ""
            color: theme.foreground
            elide: Text.ElideRight
          }
          Label {
            Layout.fillWidth: true
            text: listRow.modelData.place || ""
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideMiddle
          }
          Label {
            visible: listRow.modelData.status === "needs-input" && !!listRow.modelData.message
            Layout.fillWidth: true
            text: listRow.modelData.message || ""
            color: theme.urgent
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
          }
        }

        Label {
          Layout.alignment: Qt.AlignTop
          text: listRow.modelData.statusLabel + " · " + sessions.duration(listRow.modelData.since, page.now)
          color: page.statusColor(listRow.modelData.status)
        }

        Row {
          Layout.alignment: Qt.AlignTop
          spacing: 12
          IconButton { glyph: "󰁔"; tip: "Jump to its terminal"; onActivated: sessions.focus(listRow.modelData.id) }
          IconButton { glyph: "󰆏"; tip: "Copy path"; visible: !!listRow.modelData.cwd; onActivated: sessions.copyPath(listRow.modelData.cwd) }
          IconButton { glyph: "󰉋"; tip: "Open folder"; visible: !!listRow.modelData.cwd; onActivated: sessions.openFolder(listRow.modelData.cwd) }
          IconButton { glyph: "󰅖"; tip: "Dismiss (returns if the agent reports again)"; onActivated: sessions.dismiss(listRow.modelData.id) }
        }
      }
    }
  }

  // ---------------------------------------------------------- Grid
  GridView {
    id: gridView
    visible: page.selectedId === "" && page.grid && page.shown.length > 0
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    model: page.shown
    cellWidth: Math.max(260, Math.floor(width / Math.max(1, Math.floor(width / 280))))
    cellHeight: 150
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Item {
      id: cell
      required property var modelData
      width: gridView.cellWidth
      height: gridView.cellHeight

      Rectangle {
        anchors.fill: parent
        anchors.margins: 5
        radius: 6
        color: cardMouse.containsMouse ? theme.selection : theme.surface
        border.color: cell.modelData.status === "needs-input" ? theme.urgent : "transparent"

        MouseArea {
          id: cardMouse
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: page.selectedId = cell.modelData.id
        }

        ColumnLayout {
          anchors.fill: parent
          anchors.margins: 14
          spacing: 4

          RowLayout {
            Layout.fillWidth: true
            Rectangle { width: 10; height: 10; radius: 5; color: page.statusColor(cell.modelData.status) }
            Label {
              Layout.fillWidth: true
              text: cell.modelData.project
              color: theme.foreground
              font.bold: true
              elide: Text.ElideRight
            }
          }
          Label {
            text: cell.modelData.statusLabel + " · " + sessions.duration(cell.modelData.since, page.now)
            color: page.statusColor(cell.modelData.status)
          }
          Label {
            Layout.fillWidth: true
            visible: !!cell.modelData.branch || !!cell.modelData.modelName
            text: [cell.modelData.branch ? " " + cell.modelData.branch : "", cell.modelData.modelName || ""].filter(Boolean).join("  ·  ")
            color: theme.muted
            elide: Text.ElideRight
          }
          Label {
            Layout.fillWidth: true
            text: cell.modelData.status === "needs-input" && cell.modelData.message ? cell.modelData.message : (cell.modelData.place || "")
            color: cell.modelData.status === "needs-input" && cell.modelData.message ? theme.urgent : theme.muted
            font.pixelSize: 12
            elide: Text.ElideMiddle
          }
          Item { Layout.fillHeight: true }
          Row {
            Layout.alignment: Qt.AlignRight
            spacing: 12
            IconButton { glyph: "󰁔"; tip: "Jump to its terminal"; onActivated: sessions.focus(cell.modelData.id) }
            IconButton { glyph: "󰆏"; tip: "Copy path"; visible: !!cell.modelData.cwd; onActivated: sessions.copyPath(cell.modelData.cwd) }
            IconButton { glyph: "󰉋"; tip: "Open folder"; visible: !!cell.modelData.cwd; onActivated: sessions.openFolder(cell.modelData.cwd) }
            IconButton { glyph: "󰅖"; tip: "Dismiss (returns if the agent reports again)"; onActivated: sessions.dismiss(cell.modelData.id) }
          }
        }
      }
    }
  }
}
