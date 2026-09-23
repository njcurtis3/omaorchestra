import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Format.js" as Format

// Detail view: every session omaorchestrad knows about, the ones waiting on
// you first. BarWidget.qml owns the data; this panel only renders
// `hostWidget.sessions`.
//
// Plain Column/Item with explicit widths rather than ColumnLayout: a Layout
// whose width is bound back to the card measures zero height.
Panel {
  id: root
  moduleName: "omaorchestra.sessions"
  ipcTarget: "omaorchestra.sessions"
  manageIpc: false

  property var anchorItem: null

  // The bar identifies panels by the widget mounted in its slot, not by this
  // nested panel -- popout switching compares against that item.
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property var sessions: hostWidget ? hostWidget.sessions : []
  readonly property real now: hostWidget ? hostWidget.now : Date.now() / 1000

  readonly property int cardWidth: Style.space(340)

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(root.cardWidth)
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
    }

    Column {
      id: content
      width: parent.width
      spacing: Style.space(10)

      PanelSectionHeader { text: "Agent sessions" }

      Text {
        width: parent.width
        visible: root.sessions.length === 0
        text: "No agent sessions. Sessions appear here once omaorchestrad is running and your agent's hooks report to it."
        color: Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        wrapMode: Text.Wrap
      }

      Repeater {
        model: root.sessions

        Column {
          required property var modelData
          readonly property bool waiting: modelData.status === "needs-input"

          width: content.width
          spacing: Style.space(2)

          // "left"/"right" would shadow Item's anchor lines, hence plain Texts.
          Item {
            width: parent.width
            height: Math.max(nameText.implicitHeight, statusText.implicitHeight)

            Text {
              id: nameText
              anchors.left: parent.left
              anchors.right: statusText.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              text: Format.projectName(modelData.cwd)
              elide: Text.ElideRight
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }

            Text {
              id: statusText
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: Format.statusLabel(modelData.status)
              color: waiting ? Color.urgent : modelData.status === "working" ? Color.foreground : Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
          }

          Text {
            width: parent.width
            text: String(modelData.agent || "agent") + " · " + Format.ago(root.now - Number(modelData.updated))
              + " · " + String(modelData.cwd || "")
            elide: Text.ElideMiddle
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            visible: waiting && !!modelData.message
            text: String(modelData.message || "")
            wrapMode: Text.Wrap
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
