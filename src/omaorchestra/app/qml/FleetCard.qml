import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One decision a fleet run waits on you for (present_fleet.cards): the plan
// gate, the merge gate, files outside a slice, a slice rejected too often, a
// reply that could not be read, any other hold, its limits, or closing a
// finished run. Never a modal: it sits at the top of the run. `fleets`,
// `sessions` and `theme` come from Python.
Rectangle {
  id: card
  property var card_: ({})
  property string runId: ""
  property real now: Date.now() / 1000
  signal said(string text, bool bad)
  signal openSession(string sessionId)

  readonly property string kind: card_.kind || ""
  objectName: "card-" + kind
  implicitHeight: body.implicitHeight + 28
  radius: 6
  readonly property bool calm: kind === "close" || kind === "paused"
  color: Qt.alpha(calm ? theme.accent : theme.urgent, 0.08)
  border.color: Qt.alpha(calm ? theme.accent : theme.urgent, 0.35)
  Stripe { color: card.calm ? theme.accent : theme.urgent }

  function done(result) {
    if (result.error) card.said(result.error, true)
    else card.said(result.message || "", false)
    note.text = ""
    sendBackOpen = false
  }
  property bool sendBackOpen: false
  property bool cancelArmed: false
  property var closeResult: null
  property string reply: ""

  Timer { id: disarm; interval: 4000; onTriggered: card.cancelArmed = false }

  component Heading: Label { color: theme.muted; font.pixelSize: 12; Layout.topMargin: 6 }
  component Para: Label { Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere; color: theme.foreground }

  ColumnLayout {
    id: body
    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
    spacing: 6

    RowLayout {
      Layout.fillWidth: true
      Label {
        Layout.fillWidth: true
        text: card.card_.title || ""
        color: card.calm ? theme.accent : theme.urgent
        font.bold: true
        font.pixelSize: 16
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      }
      Label {
        visible: !!card.card_.since
        text: "waiting " + sessions.duration(card.card_.since || 0, card.now)
        color: theme.muted
        font.pixelSize: 12
      }
    }

    Para { visible: !!card.card_.text; text: card.card_.text || ""; color: card.calm ? theme.muted : theme.urgent }

    // ------------------------------------------------------ the plan
    ColumnLayout {
      visible: card.kind === "plan"
      Layout.fillWidth: true
      spacing: 4
      Para {
        text: "Shape: " + (card.card_.shape || "") + (card.card_.proposed && card.card_.proposed !== card.card_.shape
              ? " (the architect proposed " + card.card_.proposed + ")" : "")
      }
      Para { visible: !!card.card_.shapeNote; text: card.card_.shapeNote || ""; color: theme.accent }
      Para { text: card.card_.rationale || ""; color: theme.muted }
      Para { visible: !!card.card_.planKiller; text: "Plan-killer (the scout): " + (card.card_.planKiller || ""); color: theme.urgent }
      Heading { text: "Slices" }
      Repeater {
        model: card.kind === "plan" ? card.card_.slices : []
        delegate: Rectangle {
          required property var modelData
          objectName: "plan-slice-" + modelData.id
          Layout.fillWidth: true
          implicitHeight: sliceText.implicitHeight + 16
          radius: 4
          color: theme.surface
          RowLayout {
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
            ColumnLayout {
              id: sliceText
              Layout.fillWidth: true
              spacing: 2
              Label { text: modelData.id + "  " + modelData.intent; color: theme.foreground; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
              Label { text: "files: " + modelData.files.join(", "); color: theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
              Label { text: "done when: " + modelData.done_when; color: theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
              Label {
                text: "risk " + modelData.risk + ": " + modelData.risk_why
                color: modelData.risk === "high" ? theme.urgent : theme.muted
                font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere
              }
            }
            IconButton {
              objectName: "plan-drop-" + modelData.id
              Layout.alignment: Qt.AlignTop
              visible: (card.card_.slices || []).length > 1
              glyph: "󰅖"
              tip: "Drop this slice from the plan"
              onActivated: card.done(fleets.drop(card.runId, modelData.id))
            }
          }
        }
      }
      Para { visible: (card.card_.edges || []).length > 0; text: "Order: " + (card.card_.edges || []).join("; "); color: theme.muted }
      Para { visible: (card.card_.dropped || []).length > 0; text: "Dropped: " + (card.card_.dropped || []).join("; "); color: theme.muted }
      Heading { text: "Not doing" }
      Para { text: (card.card_.notDoing || []).join("; ") || "(nothing named)"; color: theme.muted }
      Heading { visible: (card.card_.risks || []).length > 0; text: "Risks the scout found" }
      Para { visible: (card.card_.risks || []).length > 0; text: (card.card_.risks || []).join("; "); color: theme.muted }
      Heading { text: "To approve" }
      Para { text: card.card_.approve || "" }
    }

    // ------------------------------------------------------ the merge
    ColumnLayout {
      visible: card.kind === "merge"
      Layout.fillWidth: true
      Para { text: "Every slice passed review. The integrator merges them into " + (card.card_.into || "the run's branch") + ", then runs the whole suite." }
      Repeater {
        model: card.card_.kind === "merge" ? card.card_.slices : []
        delegate: Para {
          required property var modelData
          text: modelData.id + "  " + modelData.intent + "   (" + modelData.branch + ")"
          color: theme.muted
        }
      }
    }

    // ------------------------------------------------------ files outside a slice
    ColumnLayout {
      visible: card.kind === "scope"
      Layout.fillWidth: true
      Para { text: (card.card_.node || "") + " changed files outside slice " + (card.card_.slice || "") + ":" }
      Repeater {
        model: card.card_.files || []
        delegate: Label { required property string modelData; text: "  " + modelData; color: theme.foreground; font.family: fontFamily; Layout.fillWidth: true; elide: Text.ElideMiddle }
      }
      Para { text: "Accept them, with the reason (the reviewer is told), or send the slice to a new builder to undo them."; color: theme.muted }
    }

    // ------------------------------------------------------ rejected too often
    RowLayout {
      visible: card.kind === "rejected"
      Layout.fillWidth: true
      spacing: 10
      Repeater {
        model: card.card_.reviews || []
        delegate: Rectangle {
          required property var modelData
          Layout.fillWidth: true
          Layout.preferredWidth: 1
          Layout.alignment: Qt.AlignTop
          implicitHeight: reviewText.implicitHeight + 16
          radius: 4
          color: theme.surface
          ColumnLayout {
            id: reviewText
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8 }
            Label { text: "Review " + modelData.attempt + ": " + modelData.verdict; color: theme.urgent; font.bold: true }
            Label { text: modelData.summary; color: theme.foreground; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }
            Repeater {
              model: modelData.findings
              delegate: Label {
                required property var modelData
                text: modelData.severity + ": " + modelData.where + ": " + modelData.what
                color: modelData.severity === "blocker" ? theme.urgent : theme.muted
                font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere
              }
            }
          }
        }
      }
    }

    // ------------------------------------------------------ a reply it could not read
    ColumnLayout {
      visible: card.kind === "bad-reply"
      Layout.fillWidth: true
      Para { text: "Try it again (with a note), or answer in its window: ask it to end with the JSON block, and its next reply is read again."; color: theme.muted }
      AppButton {
        objectName: "card-show-reply"
        text: card.reply ? "Hide its reply" : "Show its reply"
        onClicked: card.reply = card.reply ? "" : (fleets.lastReply(card.runId, card.card_.node) || "(its reply could not be read)")
      }
      Rectangle {
        visible: !!card.reply
        Layout.fillWidth: true
        implicitHeight: Math.min(260, replyText.implicitHeight + 16)
        radius: 4
        color: theme.surface
        clip: true
        ScrollView {
          anchors.fill: parent
          anchors.margins: 8
          Label { id: replyText; text: card.reply; color: theme.foreground; font.family: fontFamily; font.pixelSize: 12; wrapMode: Text.WrapAtWordBoundaryOrAnywhere; width: parent.width }
        }
      }
    }

    // ------------------------------------------------------ its limits
    RowLayout {
      visible: card.kind === "limits"
      Layout.fillWidth: true
      spacing: 8
      Label { text: "Budget US$"; color: theme.muted }
      TextField {
        id: budgetField
        objectName: "card-budget"
        implicitHeight: 32
        text: String(card.card_.budget || 0)
        implicitWidth: 80
        color: theme.foreground
        validator: DoubleValidator { bottom: 0; top: 10000 }
        background: Rectangle { radius: 4; color: theme.surface; border.color: theme.selection }
      }
      Label { text: "Steps"; color: theme.muted }
      TextField {
        id: stepsField
        objectName: "card-steps"
        implicitHeight: 32
        text: String(card.card_.steps || 30)
        implicitWidth: 60
        color: theme.foreground
        validator: IntValidator { bottom: 3; top: 200 }
        background: Rectangle { radius: 4; color: theme.surface; border.color: theme.selection }
      }
      Label {
        Layout.fillWidth: true
        text: "spent $" + (card.card_.spent || 0).toFixed(2) + ", " + (card.card_.used || 0) + " steps used"
        color: theme.muted
      }
      AppButton {
        objectName: "card-limits"
        primary: true
        text: "Raise"
        onClicked: card.done(fleets.limits(card.runId, Number(budgetField.text), Number(stepsField.text)))
      }
    }

    // ------------------------------------------------------ closing
    ColumnLayout {
      visible: card.kind === "close"
      Layout.fillWidth: true
      Para { text: "Closing checks git (every slice built, reviewed PASS and on " + (card.card_.branch || "the run's branch") + ", nothing uncommitted, nothing outside the slices), removes the slices' worktrees, and leaves the branch for you to merge from Worktrees." ; color: theme.muted }
      Repeater {
        model: card.closeResult ? card.closeResult.checks || [] : []
        delegate: Label {
          required property var modelData
          text: (modelData.ok ? "✓  " : "✗  ") + modelData.text
          color: modelData.ok ? theme.foreground : theme.urgent
          Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        }
      }
    }

    // ------------------------------------------------------ a note, for what takes one
    TextField {
      id: note
      objectName: "card-note"
      implicitHeight: 32
      visible: card.sendBackOpen || ["scope", "rejected", "bad-reply", "held"].indexOf(card.kind) >= 0
      Layout.fillWidth: true
      placeholderText: card.kind === "scope" ? "Why they belong (required to accept)"
                       : card.sendBackOpen ? "What should change (required)" : "A note for the next try (optional)"
      placeholderTextColor: theme.muted
      color: theme.foreground
      background: Rectangle { radius: 4; color: theme.surface; border.color: note.activeFocus ? theme.accent : theme.selection }
    }

    // ------------------------------------------------------ what can be done
    Flow {
      Layout.fillWidth: true
      Layout.topMargin: 4
      spacing: 8

      AppButton {
        objectName: "card-approve"
        visible: card.kind === "plan" || card.kind === "merge"
        primary: true
        text: card.kind === "plan" ? "Approve: builders start" : "Approve the merge"
        onClicked: card.done(fleets.approve(card.runId, card.kind, ""))
      }
      AppButton {
        objectName: "card-send-back"
        visible: card.kind === "plan"
        text: card.sendBackOpen ? "Send it back" : "Send back…"
        enabled: !card.sendBackOpen || note.text.trim() !== ""
        onClicked: if (!card.sendBackOpen) { card.sendBackOpen = true; note.forceActiveFocus() }
                   else card.done(fleets.sendBack(card.runId, note.text))
      }
      AppButton {
        objectName: "card-single-loop"
        visible: card.kind === "plan" && card.card_.shape === "diamond"
        text: "Run as single loop"
        onClicked: card.done(fleets.shape(card.runId, "single-loop"))
      }
      AppButton {
        objectName: "card-diamond"
        visible: card.kind === "plan" && card.card_.shape !== "diamond"
        text: "Try as diamond"
        onClicked: card.done(fleets.shape(card.runId, "diamond"))
      }
      AppButton {
        objectName: "card-resume"
        visible: card.kind === "paused"
        primary: true
        text: "Resume"
        onClicked: card.done(fleets.pause(card.runId, false))
      }
      AppButton {
        objectName: "card-accept"
        visible: card.kind === "scope"
        primary: true
        enabled: note.text.trim() !== ""
        text: "Accept them"
        onClicked: card.done(fleets.acceptFiles(card.runId, card.card_.node, note.text))
      }
      AppButton {
        objectName: "card-undo"
        visible: card.kind === "scope"
        text: "Send back to undo them"
        onClicked: card.done(fleets.undoFiles(card.runId, card.card_.node))
      }
      AppButton {
        objectName: "card-retry"
        visible: ["rejected", "bad-reply", "held"].indexOf(card.kind) >= 0
        primary: true
        text: card.kind === "rejected" ? "Another try" : "Try again"
        onClicked: card.done(fleets.retry(card.runId, note.text))
      }
      AppButton {
        objectName: "card-take-over"
        visible: card.kind === "rejected" && !!card.card_.session
        text: "Take over"
        onClicked: fleets.takeOver(card.card_.session)
        ToolTip.visible: hovered
        ToolTip.text: "Bring the last builder's window forward and work in it yourself; then Another try, or approve its review."
      }
      AppButton {
        objectName: "card-open-session"
        visible: ["scope", "bad-reply", "held"].indexOf(card.kind) >= 0 && !!card.card_.session
        text: "Open its session"
        onClicked: card.openSession(card.card_.session)
      }
      AppButton {
        objectName: "card-check"
        visible: card.kind === "close"
        text: "Check it"
        onClicked: {
          const result = fleets.closeCheck(card.runId)
          if (result.error) card.said(result.error, true)
          card.closeResult = result.error ? null : result
        }
      }
      AppButton {
        objectName: "card-close"
        visible: card.kind === "close"
        primary: true
        enabled: !!card.closeResult && card.closeResult.ready
        text: "Close it"
        onClicked: {
          const result = fleets.close(card.runId)
          card.said(result.error || ("Closed. " + (result.notes || []).join(" ")), !!result.error)
        }
      }
      AppButton {
        objectName: "card-cancel"
        visible: card.kind !== "close"
        danger: true
        text: card.cancelArmed ? "Really cancel the run?" : "Cancel run"
        onClicked: {
          if (!card.cancelArmed) { card.cancelArmed = true; disarm.restart(); return }
          card.cancelArmed = false
          card.done(fleets.cancel(card.runId))
        }
      }
    }
  }
}
