import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A run as a table: one row per slice (fleet.board), its latest build and
// review, tries, the scope check, cost and branch; a split slice's smaller
// slices sit under it, indented. Rows update in place
// (a keyed model), so a picked row stays picked. `fleets` and `theme` come
// from Python.
ColumnLayout {
  id: board
  property string runId: ""
  property string chosen: ""  // a slice id
  signal pick(string sliceId, string nodeId)  // its latest build, "" before one
  spacing: 4

  readonly property var widths: [80, 130, 90, 60, 80]

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
      readonly property bool bad: item.extra.length && !item.accepted || !!item.blocked
      // Not started yet: dimmed, so progress shows before reading a word.
      readonly property bool pending: item.build === "not started"
      readonly property color base: board.chosen === key ? theme.selection
                                    : rowMouse.containsMouse ? Qt.tint(theme.surface, Qt.alpha(theme.selection, 0.5)) : theme.surface
      color: bad || item.waiting || item.stalled ? Qt.tint(base, Qt.alpha(theme.urgent, 0.08)) : base
      opacity: pending && board.chosen !== key && !rowMouse.containsMouse ? 0.6 : 1
      Behavior on color { ColorAnimation { duration: 120 } }
      Behavior on opacity { NumberAnimation { duration: 120 } }

      Stripe {
        shown: boardRow.bad || boardRow.item.waiting || boardRow.item.stalled || board.chosen === boardRow.key
        color: boardRow.bad || boardRow.item.waiting || boardRow.item.stalled ? theme.urgent : theme.accent
      }

      MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: board.pick(boardRow.key, boardRow.item.node)
      }

      ColumnLayout {
        id: rowBody
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 10; rightMargin: 10 }
        spacing: 2
        RowLayout {
          spacing: 8
          Label { text: boardRow.key; leftPadding: boardRow.item.depth * 12; color: theme.foreground; font.bold: !boardRow.item.depth; Layout.fillWidth: true; Layout.preferredWidth: board.widths[0]; Layout.maximumWidth: board.widths[0]; elide: Text.ElideRight }
          Label {
            text: boardRow.item.build + (boardRow.item.waiting ? " · you" : boardRow.item.stalled ? " · stalled" : "")
            color: boardRow.item.waiting || boardRow.item.stalled ? theme.urgent : boardRow.item.build === "running" ? theme.accent : theme.foreground
            Layout.fillWidth: true
            Layout.preferredWidth: board.widths[1]
            Layout.maximumWidth: board.widths[1]
            elide: Text.ElideRight
          }
          Label {
            text: boardRow.item.verdict === "PASS" ? "✓ PASS" : boardRow.item.verdict === "REJECT" ? "✗ REJECT" : boardRow.item.verdict || "-"
            color: boardRow.item.verdict === "REJECT" ? theme.urgent : boardRow.item.verdict === "PASS" ? theme.accent : theme.muted
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
