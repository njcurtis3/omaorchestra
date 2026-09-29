import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A run as a table: one row per slice (fleet.board), its latest build and
// review, tries, the scope check, cost and branch. Rows update in place
// (a keyed model), so a picked row stays picked. `fleets` and `theme` come
// from Python.
ColumnLayout {
  id: board
  property string runId: ""
  property string chosen: ""  // a slice id
  signal pick(string sliceId)
  spacing: 4

  readonly property var widths: [60, 130, 90, 60, 80]

  RowLayout {
    Layout.fillWidth: true
    Layout.leftMargin: 10
    spacing: 8
    Repeater {
      model: ["Slice", "Build", "Review", "Tries", "Cost"]
      delegate: Label {
        required property string modelData
        required property int index
        text: modelData
        color: theme.muted
        font.pixelSize: 12
        Layout.fillWidth: true
        Layout.preferredWidth: board.widths[index]
        Layout.maximumWidth: board.widths[index]
        elide: Text.ElideRight
      }
    }
  }

  Repeater {
    model: board.runId ? fleets.board(board.runId) : null
    delegate: Rectangle {
      id: boardRow
      required property var item
      required property string key
      objectName: "board-" + key
      Layout.fillWidth: true
      implicitHeight: rowBody.implicitHeight + 14
      radius: 4
      color: board.chosen === key ? theme.selection : rowMouse.containsMouse ? Qt.alpha(theme.selection, 0.5) : theme.surface
      border.color: item.extra.length && !item.accepted || item.blocked ? theme.urgent : "transparent"

      MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: board.pick(boardRow.key)
      }

      ColumnLayout {
        id: rowBody
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 10; rightMargin: 10 }
        spacing: 2
        RowLayout {
          spacing: 8
          Label { text: boardRow.key; color: theme.foreground; font.bold: true; Layout.fillWidth: true; Layout.preferredWidth: board.widths[0]; Layout.maximumWidth: board.widths[0]; elide: Text.ElideRight }
          Label {
            text: boardRow.item.build + (boardRow.item.waiting ? " · you" : boardRow.item.stalled ? " · stalled" : "")
            color: boardRow.item.waiting || boardRow.item.stalled ? theme.urgent : boardRow.item.build === "running" ? theme.accent : theme.foreground
            Layout.fillWidth: true
            Layout.preferredWidth: board.widths[1]
            Layout.maximumWidth: board.widths[1]
            elide: Text.ElideRight
          }
          Label {
            text: boardRow.item.verdict || "-"
            color: boardRow.item.verdict === "REJECT" ? theme.urgent : boardRow.item.verdict === "PASS" ? theme.foreground : theme.muted
            Layout.fillWidth: true
            Layout.preferredWidth: board.widths[2]
            Layout.maximumWidth: board.widths[2]
            elide: Text.ElideRight
          }
          Label { text: String(boardRow.item.tries); color: theme.muted; Layout.fillWidth: true; Layout.preferredWidth: board.widths[3]; Layout.maximumWidth: board.widths[3]; elide: Text.ElideRight }
          Label { text: "$" + boardRow.item.cost.toFixed(2); color: theme.muted; Layout.fillWidth: true; Layout.preferredWidth: board.widths[4]; Layout.maximumWidth: board.widths[4]; elide: Text.ElideRight }
        }
        Label {
          Layout.fillWidth: true
          text: boardRow.item.intent + (boardRow.item.branch ? "   ·   " + boardRow.item.branch : "")
          color: theme.muted
          font.pixelSize: 12
          elide: Text.ElideMiddle
        }
        Label {
          visible: boardRow.item.extra.length > 0 || !!boardRow.item.blocked
          Layout.fillWidth: true
          wrapMode: Text.WrapAtWordBoundaryOrAnywhere
          font.pixelSize: 12
          color: boardRow.item.accepted ? theme.muted : theme.urgent
          text: boardRow.item.blocked ? "blocked: " + boardRow.item.blocked
                : (boardRow.item.accepted ? "accepted outside its files: " : "outside its files: ") + boardRow.item.extra.join(", ")
        }
      }
    }
  }
}
