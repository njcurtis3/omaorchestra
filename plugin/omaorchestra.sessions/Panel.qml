import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Format.js" as Format

// Detail view: every session omaorchestrad knows about, the ones waiting on
// you first. Each row has copy-path, open-folder and dismiss icons; clicking
// anywhere else on it runs `omaorchestra focus <id>`, which (like dismiss) needs
// omaorchestra on PATH (/usr/bin when packaged; scripts/dev-install
// links a checkout into ~/.local/bin). BarWidget.qml owns the data; this panel only renders
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
  readonly property var away: hostWidget ? hostWidget.away : null
  readonly property real now: hostWidget ? hostWidget.now : Date.now() / 1000

  readonly property int cardWidth: Style.space(340)

  // A small glyph button: muted until hovered.
  component ActionIcon: Text {
    id: icon
    property string glyph: ""
    property string tip: ""
    signal activated()

    text: glyph
    color: !enabled ? Color.muted : iconMouse.containsMouse ? Color.foreground : Color.muted
    opacity: enabled ? 1 : 0.4
    font.family: Style.font.family
    font.pixelSize: Style.font.body

    MouseArea {
      id: iconMouse
      anchors.fill: parent
      anchors.margins: -Style.space(3)
      hoverEnabled: true
      enabled: icon.enabled
      cursorShape: Qt.PointingHandCursor
      onClicked: icon.activated()
    }
  }

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

      Item {
        width: parent.width
        height: Math.max(sectionHeader.implicitHeight, openApp.implicitHeight)

        PanelSectionHeader {
          id: sectionHeader
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: "Agent sessions"
        }

        ActionIcon {
          id: openApp
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          glyph: "󰖯"
          tip: "Open the omaorchestra app"
          onActivated: {
            Quickshell.execDetached(["omaorchestra", "app"])
            root.close()
          }
        }
      }

      // Away mode: where you are, and the switch.
      Item {
        width: parent.width
        visible: Format.awayText(root.away) !== ""
        height: visible ? Math.max(awayLine.implicitHeight, modes.implicitHeight) : 0

        Text {
          id: awayLine
          anchors.left: parent.left
          anchors.right: modes.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          text: "󰄜  " + Format.awayText(root.away)
          color: Format.awayNow(root.away) ? Color.foreground : Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Row {
          id: modes
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(8)

          Repeater {
            model: ["auto", "on", "off"]

            Text {
              required property string modelData
              readonly property bool current: root.away && root.away.mode === modelData
              text: modelData
              color: current ? Color.accent : modeMouse.containsMouse ? Color.foreground : Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.underline: current

              MouseArea {
                id: modeMouse
                anchors.fill: parent
                anchors.margins: -Style.space(3)
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: Quickshell.execDetached(["omaorchestra", "away", parent.modelData])
              }
            }
          }
        }
      }

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

        // Click a session to jump to its terminal window.
        Item {
          id: row
          required property var modelData
          readonly property bool waiting: modelData.status === "needs-input"

          width: content.width
          height: rowContent.implicitHeight

          MouseArea {
            id: rowMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              Quickshell.execDetached(["omaorchestra", "focus", row.modelData.id])
              root.close()
            }
          }

          Column {
            id: rowContent
            width: parent.width
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
                text: Format.projectName(row.modelData.cwd)
                elide: Text.ElideRight
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.underline: rowMouse.containsMouse
              }

              Text {
                id: statusText
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: Format.statusLabel(row.modelData.status)
                color: row.waiting ? Color.urgent : row.modelData.status === "working" ? Color.foreground : Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
            }

            Item {
              width: parent.width
              height: Math.max(metaText.implicitHeight, actions.implicitHeight)

              Text {
                id: metaText
                anchors.left: parent.left
                anchors.right: actions.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                text: String(row.modelData.agent || "agent")
                  + (row.modelData.model ? " · " + Format.modelName(row.modelData.model) : "")
                  + " · " + Format.ago(root.now - Number(row.modelData.updated))
                  + " · " + String(row.modelData.cwd || "")
                elide: Text.ElideMiddle
                color: Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }

              // Declared after the row's MouseArea, so these take the click.
              Row {
                id: actions
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(10)

                ActionIcon {
                  id: copyIcon
                  glyph: copied ? "󰄬" : "󰆏"
                  tip: "Copy path"
                  enabled: !!row.modelData.cwd
                  property bool copied: false
                  onActivated: {
                    Quickshell.execDetached(["wl-copy", "--", String(row.modelData.cwd)])
                    copied = true
                    copiedReset.restart()
                  }
                  Timer { id: copiedReset; interval: 1200; onTriggered: copyIcon.copied = false }
                }
                ActionIcon {
                  glyph: "󰉋"
                  tip: "Open folder"
                  enabled: !!row.modelData.cwd
                  onActivated: {
                    Quickshell.execDetached(["xdg-open", String(row.modelData.cwd)])
                    root.close()
                  }
                }
                ActionIcon {
                  glyph: "󰅖"
                  tip: "Dismiss (returns if the agent reports again)"
                  onActivated: Quickshell.execDetached(["omaorchestra", "dismiss", row.modelData.id])
                }
              }
            }

            Text {
              width: parent.width
              visible: row.waiting && !!row.modelData.message
              text: String(row.modelData.message || "")
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
}
