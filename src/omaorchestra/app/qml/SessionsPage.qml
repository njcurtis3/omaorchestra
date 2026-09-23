import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The sessions dashboard: filters, then every session as a list or a grid,
// waiting first. Click a session to jump to its terminal. `sessions` and
// `theme` come from Python; `now` ticks from the window.
ColumnLayout {
  id: page
  property real now: Date.now() / 1000
  property string statusFilter: ""
  property bool grid: false

  spacing: 16

  // Re-read whenever the sessions change (sessions.rows notifies) or a filter does.
  readonly property var shown: {
    sessions.rows
    return sessions.filtered(statusFilter, search.text)
  }

  function statusColor(status) {
    return status === "needs-input" ? theme.urgent : status === "working" ? theme.accent : theme.muted
  }

  // ---------------------------------------------------------- Filters
  RowLayout {
    Layout.fillWidth: true
    spacing: 8

    Repeater {
      model: [
        { label: "All", status: "", count: sessions.total },
        { label: "Waiting", status: "needs-input", count: sessions.waiting },
        { label: "Working", status: "working", count: sessions.working },
        { label: "Idle", status: "idle", count: sessions.idle }
      ]

      delegate: Button {
        required property var modelData
        readonly property bool selected: page.statusFilter === modelData.status
        text: modelData.label + "  " + modelData.count
        flat: true
        onClicked: page.statusFilter = modelData.status
        contentItem: Label {
          text: parent.text
          color: parent.selected ? theme.foreground
                 : modelData.status === "needs-input" && modelData.count > 0 ? theme.urgent : theme.muted
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
      Layout.fillWidth: true
      Layout.leftMargin: 8
      placeholderText: "Filter by project, path, branch or model"
      placeholderTextColor: theme.muted
      color: theme.foreground
      selectionColor: theme.selection
      background: Rectangle { radius: 4; color: theme.surface; border.color: search.activeFocus ? theme.accent : theme.selection }
    }

    IconButton {
      glyph: page.grid ? "󰕰" : "󰕮"
      tip: page.grid ? "Show as a list" : "Show as a grid"
      onActivated: page.grid = !page.grid
    }
  }

  // ---------------------------------------------------------- Empty states
  Label {
    visible: page.shown.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 24
    horizontalAlignment: Text.AlignHCenter
    color: theme.muted
    text: sessions.total === 0
      ? "No agent sessions. They appear here as soon as an agent starts."
      : "No sessions match."
  }

  // ---------------------------------------------------------- List
  ListView {
    visible: !page.grid && page.shown.length > 0
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
      width: ListView.view.width
      height: listContent.implicitHeight + 20
      radius: 6
      color: rowMouse.containsMouse ? theme.selection : theme.surface

      MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: sessions.focus(listRow.modelData.id)
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

          RowLayout {
            spacing: 10
            Label { text: listRow.modelData.project; color: theme.foreground; font.bold: true }
            Label {
              visible: !!listRow.modelData.branch
              text: " " + (listRow.modelData.branch || "")
              color: theme.muted
            }
            Label {
              visible: !!listRow.modelData.modelName
              text: listRow.modelData.modelName
              color: theme.muted
            }
          }
          Label {
            Layout.fillWidth: true
            text: listRow.modelData.cwd || ""
            color: theme.muted
            font.pixelSize: 12
            elide: Text.ElideMiddle
          }
          Label {
            visible: listRow.modelData.status === "needs-input" && !!listRow.modelData.message
            Layout.fillWidth: true
            text: listRow.modelData.message || ""
            color: theme.urgent
            wrapMode: Text.Wrap
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
    visible: page.grid && page.shown.length > 0
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
          onClicked: sessions.focus(cell.modelData.id)
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
            text: cell.modelData.status === "needs-input" && cell.modelData.message ? cell.modelData.message : (cell.modelData.cwd || "")
            color: cell.modelData.status === "needs-input" && cell.modelData.message ? theme.urgent : theme.muted
            font.pixelSize: 12
            elide: Text.ElideMiddle
          }
          Item { Layout.fillHeight: true }
          Row {
            Layout.alignment: Qt.AlignRight
            spacing: 12
            IconButton { glyph: "󰆏"; tip: "Copy path"; visible: !!cell.modelData.cwd; onActivated: sessions.copyPath(cell.modelData.cwd) }
            IconButton { glyph: "󰉋"; tip: "Open folder"; visible: !!cell.modelData.cwd; onActivated: sessions.openFolder(cell.modelData.cwd) }
            IconButton { glyph: "󰅖"; tip: "Dismiss (returns if the agent reports again)"; onActivated: sessions.dismiss(cell.modelData.id) }
          }
        }
      }
    }
  }
}
