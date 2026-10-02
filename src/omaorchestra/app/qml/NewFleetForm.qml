import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Start a fleet run: the goal, the folder, which fleet, and optionally a
// budget and a shape. A short goal gets a gentle word about the stop rule
// (a single task is probably enough), never a refusal. `fleets`,
// `sessions` and `theme` come from Python.
ColumnLayout {
  id: form
  spacing: 12
  signal started(string runId)
  signal cancelled()

  property string error: ""
  readonly property bool folderOk: sessions.folderExists(folder.text)
  readonly property bool ready: goal.text.trim() !== "" && folderOk

  function prefill(text, where) {
    goal.text = text || ""
    if (where) folder.text = where
    goal.forceActiveFocus()
  }
  function start() {
    if (!ready) return
    const budget = budgetField.text.trim() === "" ? -1 : Number(budgetField.text)
    const result = fleets.start(goal.text, folder.text, fleetBox.currentValue || "auto", budget, shapeBox.currentValue || "")
    if (result.error) { error = result.error; return }
    error = ""
    goal.text = ""
    started(result.id)
  }

  onVisibleChanged: if (visible) {
    const t = fleets.templates()
    fleetBox.model = t.templates.map(x => ({ value: x.name, label: x.name, description: x.description }))
    fleetBox.currentIndex = Math.max(0, fleetBox.model.findIndex(x => x.value === "auto"))
    templateError.text = t.error || ""
    if (!folder.text) {
      const recent = sessions.recentFolders()
      if (recent.length) folder.text = recent[0]
    }
    goal.forceActiveFocus()
  }

  component FieldLabel: Label { color: theme.muted }

  RowLayout {
    spacing: 10
    Label {
      text: "New fleet run"
      color: theme.foreground
      font.pixelSize: 18
      font.bold: true
    }
    Label {
      objectName: "fleet-experimental"
      text: "experimental"
      color: theme.accent
      font.pixelSize: 12
      leftPadding: 6; rightPadding: 6; topPadding: 1; bottomPadding: 1
      background: Rectangle { radius: 8; color: "transparent"; border.color: theme.accent }
      ToolTip.visible: tagMouse.containsMouse
      ToolTip.text: "Every part is tested, but live runs are only beginning: start with a small goal in a repository you can reset."
      MouseArea { id: tagMouse; anchors.fill: parent; hoverEnabled: true }
    }
  }
  Label {
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.muted
    text: "A scout looks first, an architect writes a plan of slices, and nothing is built until you approve it. "
          + "Then builders and reviewers take the slices, one at a time or in parallel."
  }

  FieldLabel { text: "Goal" }
  Rectangle {
    Layout.fillWidth: true
    Layout.preferredHeight: 110
    radius: 4
    color: theme.surface
    border.color: goal.activeFocus ? theme.accent : theme.selection
    ScrollView {
      anchors.fill: parent
      anchors.margins: 2
      TextArea {
        id: goal
        objectName: "fleet-goal"
        wrapMode: TextEdit.Wrap
        color: theme.foreground
        placeholderText: "What should the run achieve?"
        placeholderTextColor: theme.muted
        background: null
      }
    }
  }
  Label {
    objectName: "fleet-hint"
    visible: text !== ""
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.accent
    text: fleets.stopRuleHint(goal.text)
  }

  FieldLabel { text: "Folder" }
  TextField {
    id: folder
    objectName: "fleet-folder"
    implicitHeight: 32
    Layout.fillWidth: true
    color: theme.foreground
    placeholderText: "~/code/app"
    placeholderTextColor: theme.muted
    background: Rectangle { radius: 4; color: theme.surface; border.color: form.folderOk || !folder.text ? theme.selection : theme.urgent }
  }

  Flow {
    Layout.fillWidth: true
    spacing: 16
    ColumnLayout {
      FieldLabel { text: "Fleet" }
      AppComboBox { id: fleetBox; objectName: "fleet-template"; textRole: "label"; valueRole: "value" }
    }
    ColumnLayout {
      FieldLabel { text: "Shape" }
      AppComboBox {
        id: shapeBox
        objectName: "fleet-shape"
        textRole: "label"
        valueRole: "value"
        model: [{ value: "", label: "The fleet's (or the architect's)" }, { value: "single-loop", label: "Single loop" },
                { value: "diamond", label: "Diamond (checked)" }]
      }
    }
    ColumnLayout {
      FieldLabel { text: "Budget US$" }
      TextField {
        id: budgetField
        objectName: "fleet-budget"
        implicitHeight: 32
        implicitWidth: 110
        color: theme.foreground
        placeholderText: "the fleet's"
        placeholderTextColor: theme.muted
        validator: DoubleValidator { bottom: 0; top: 10000 }
        background: Rectangle { radius: 4; color: theme.surface; border.color: theme.selection }
      }
    }
  }
  Label {
    objectName: "fleet-description"
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.muted
    text: fleetBox.currentIndex >= 0 && fleetBox.model.length ? fleetBox.model[fleetBox.currentIndex].description : ""
  }
  Label { id: templateError; visible: text !== ""; color: theme.urgent; Layout.fillWidth: true; wrapMode: Text.WrapAtWordBoundaryOrAnywhere }

  Label {
    visible: !!form.error
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.urgent
    text: form.error
  }

  // The note beside the buttons, or above them in a narrow window.
  GridLayout {
    Layout.fillWidth: true
    columns: form.width >= 640 ? 2 : 1
    columnSpacing: 16
    rowSpacing: 8
    Label {
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.muted
      font.pixelSize: 12
      text: "Each agent is a queued task in its own terminal: the parallel limit, the daily budget and usage limits apply. The first time an agent works in a folder it asks whether to trust it; answer there."
    }
    RowLayout {
      Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
      spacing: 8
      AppButton { text: "Cancel"; onClicked: form.cancelled() }
      AppButton {
        objectName: "fleet-start"
        primary: true
        enabled: form.ready
        text: "Start the run"
        onClicked: form.start()
      }
    }
  }
  Item { Layout.fillHeight: true }
}
