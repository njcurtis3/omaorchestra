import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// Start an agent on a task. On launch the window switches to the new
// session's details. `sessions` and `theme` come from Python.
ColumnLayout {
  id: page
  spacing: 14
  signal launched(string sessionId)
  signal queued()
  signal asFleet(string goal, string folder)

  property string error: ""
  readonly property bool folderOk: sessions.folderExists(folder.text)
  readonly property bool inRepo: folderOk && sessions.inGitRepo(folder.text)
  readonly property bool ready: prompt.text.trim() !== "" && folderOk
  // A chain: a recipe, or a follow-up task ("Then…"). It is always queued.
  readonly property string recipe: recipeBox.currentValue || ""
  readonly property bool chained: recipe !== "" || thenText.text.trim() !== ""

  function reset() {
    prompt.text = ""
    thenText.text = ""
    thenOpen = false
    scheduleOpen = false
    whenField.text = ""
    rememberBox.checked = false
    error = ""
    prompt.forceActiveFocus()
  }
  property bool thenOpen: false
  function startChain() {
    let result
    if (recipe) {
      result = queue.runRecipe(recipe, prompt.text, folder.text, modelBox.value, page.inRepo && worktreeSwitch.checked,
                               page.routing ? providerBox.currentValue || "" : "")
    } else {
      result = queue.addChain(prompt.text, thenText.text, folder.text, modelBox.value, permissionBox.currentValue,
                              page.inRepo && worktreeSwitch.checked, page.routing ? providerBox.currentValue || "" : "",
                              page.mcpProfiles ? mcpBox.currentValue || "" : "", agentBox.currentValue || "claude")
    }
    if (result.error) { error = result.error; return }
    reset()
    queued()
  }
  function addToQueue() {
    if (!ready) return
    if (chained) { startChain(); return }
    if (rememberBox.checked && !providerBox.currentValue) sessions.rememberModel(folder.text, modelBox.value)
    const result = queue.add(prompt.text, folder.text, modelBox.value, permissionBox.currentValue,
                             page.inRepo && worktreeSwitch.checked, false, page.routing ? providerBox.currentValue || "" : "",
                             page.mcpProfiles ? mcpBox.currentValue || "" : "", agentBox.currentValue || "claude")
    if (result.error) { error = result.error; return }
    reset()
    queued()
  }
  // Schedule: queue it on a timetable instead (schedules.py). A recipe is
  // scheduled as its chain; a "Then…" follow-up is not.
  property bool scheduleOpen: false
  readonly property var whenCheck: schedules.check(whenField.text)
  readonly property bool canSchedule: ready && thenText.text.trim() === "" && !whenCheck.error
  function addSchedule() {
    if (!canSchedule) return
    const result = schedules.add(whenField.text, prompt.text, folder.text, modelBox.value, permissionBox.currentValue,
                                 page.inRepo && worktreeSwitch.checked, page.routing ? providerBox.currentValue || "" : "",
                                 page.mcpProfiles ? mcpBox.currentValue || "" : "", agentBox.currentValue || "claude",
                                 recipe)
    if (result.error) { error = result.error; return }
    reset()
    queued()
  }
  function launch() {
    if (!ready) return
    if (chained) { startChain(); return }
    if (rememberBox.checked && !providerBox.currentValue) sessions.rememberModel(folder.text, modelBox.value)
    const result = sessions.launch(prompt.text, folder.text, modelBox.value, permissionBox.currentValue,
                                   page.inRepo && worktreeSwitch.checked, page.routing ? providerBox.currentValue || "" : "",
                                   page.mcpProfiles ? mcpBox.currentValue || "" : "", agentBox.currentValue || "claude")
    if (result.error) { error = result.error; return }
    const id = result.id
    reset()
    launched(id)
  }

  // What the chosen agent supports.
  readonly property var agentInfo: agentBox.currentIndex >= 0 && agentBox.model.length ? agentBox.model[agentBox.currentIndex] : null
  readonly property bool routing: !agentInfo || agentInfo.routing
  readonly property bool mcpProfiles: !agentInfo || agentInfo.mcpProfile

  onVisibleChanged: if (visible) {
    agentBox.model = sessions.agentChoices()
    providerBox.model = sessions.providerChoices()
    mcpBox.model = mcp.profileChoices()
    worktreeSwitch.checked = sessions.worktreeDefault()
    recentList.model = sessions.recentFolders()
    if (!folder.text && recentList.model.length) folder.text = recentList.model[0]
    prompt.forceActiveFocus()
  }

  component FieldLabel: Label { color: theme.muted }
  component Field: Rectangle {
    radius: 4
    color: theme.surface
    border.color: theme.selection
  }

  // ---------------------------------------------------------- Task
  FieldLabel { text: "Task" }
  Field {
    Layout.fillWidth: true
    Layout.preferredHeight: 150
    border.color: prompt.activeFocus ? theme.accent : theme.selection
    ScrollView {
      anchors.fill: parent
      anchors.margins: 2
      TextArea {
        id: prompt
        objectName: "task-prompt"
        wrapMode: TextEdit.Wrap
        color: theme.foreground
        selectionColor: theme.selection
        placeholderText: "What should the agent do?  (Ctrl+Enter to launch)"
        placeholderTextColor: theme.muted
        background: null
        Keys.onPressed: event => {
          if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
            page.launch()
            event.accepted = true
          }
        }
      }
    }
  }

  // ---------------------------------------------------------- Then…
  AppButton {
    objectName: "task-then-open"
    visible: !page.thenOpen && !page.recipe
    text: "Then…"
    quiet: true
    leftPadding: 0
    onClicked: { page.thenOpen = true; thenText.forceActiveFocus() }
    ToolTip.visible: hovered
    ToolTip.delay: 500
    ToolTip.text: "Add a follow-up task that starts in the same worktree once this one finishes"
  }
  FieldLabel { visible: page.thenOpen && !page.recipe; text: "Then, once that finishes, in the same worktree" }
  Field {
    visible: page.thenOpen && !page.recipe
    Layout.fillWidth: true
    Layout.preferredHeight: 80
    border.color: thenText.activeFocus ? theme.accent : theme.selection
    ScrollView {
      anchors.fill: parent
      anchors.margins: 2
      TextArea {
        id: thenText
        objectName: "task-then"
        wrapMode: TextEdit.Wrap
        color: theme.foreground
        selectionColor: theme.selection
        placeholderText: "e.g. Write tests for it and commit them. (It gets a brief of what the first task did.)"
        placeholderTextColor: theme.muted
        background: null
      }
    }
  }

  // ---------------------------------------------------------- Folder
  FieldLabel { text: "Folder" }
  RowLayout {
    Layout.fillWidth: true
    spacing: 8
    TextField {
      id: folder
      objectName: "task-folder"
      implicitHeight: 32
      Layout.fillWidth: true
      color: theme.foreground
      selectionColor: theme.selection
      placeholderText: "~/code/project"
      placeholderTextColor: theme.muted
      background: Rectangle { radius: 4; color: theme.surface; border.color: folder.text && !page.folderOk ? theme.urgent : folder.activeFocus ? theme.accent : theme.selection }
    }
    AppButton {
      text: "Browse…"
      onClicked: folderDialog.open()
    }
  }
  Label {
    visible: !!folder.text && !page.folderOk
    color: theme.urgent
    text: "That folder does not exist."
  }
  Flow {
    Layout.fillWidth: true
    spacing: 8
    Repeater {
      id: recentList
      model: []
      delegate: Chip {
        required property string modelData
        text: modelData.replace(/^\/home\/[^/]+/, "~")
        selected: folder.text === modelData
        onClicked: folder.text = modelData
        width: Math.min(implicitWidth, parent.width)
      }
    }
  }

  // ---------------------------------------------------------- Options
  // A Flow, so the options wrap onto a second line in a narrow window
  // instead of stretching the page (and pushing the buttons off-screen).
  Flow {
    Layout.fillWidth: true
    spacing: 24

    ColumnLayout {
      FieldLabel { text: "Model" }
      AppComboBox {
        id: modelBox
        objectName: "task-model"
        Layout.preferredWidth: 220
        editable: true
        model: sessions.modelChoices(folder.text, providerBox.currentValue || "", agentBox.currentValue || "claude")
        textRole: "label"
        valueRole: "value"
        // A chosen entry gives its alias; typed text is passed as the model name.
        readonly property string value: currentIndex >= 0 && editText === currentText ? currentValue : editText.trim()
      }
    }

    // Under an empty label, so it lines up with the model box beside it
    // (a Flow ignores Layout.alignment).
    ColumnLayout {
      FieldLabel { text: " " }
      AppCheckBox {
        id: rememberBox
        objectName: "task-remember-model"
        implicitHeight: 32
        enabled: page.folderOk && !providerBox.currentValue && (agentBox.currentValue || "claude") === "claude"
        text: "Remember for this folder"
      }
    }

    ColumnLayout {
      FieldLabel { text: "Recipe" }
      AppComboBox {
        id: recipeBox
        objectName: "task-recipe"
        model: queue.recipeChoices()
        textRole: "label"
        valueRole: "value"
        ToolTip.visible: hovered && currentIndex > 0
        ToolTip.delay: 500
        ToolTip.text: currentIndex >= 0 && model.length ? model[currentIndex].description : ""
      }
    }

    ColumnLayout {
      FieldLabel { text: "Permissions" }
      AppComboBox {
        id: permissionBox
        objectName: "task-permissions"
        model: sessions.permissionChoices
        textRole: "label"
        valueRole: "value"
      }
    }

    ColumnLayout {
      FieldLabel { text: "Agent" }
      AppComboBox {
        id: agentBox
        objectName: "task-agent"
        model: [{ value: "claude", label: "Claude Code", routing: true, mcpProfile: true }]
        textRole: "label"
        valueRole: "value"
      }
    }

    ColumnLayout {
      FieldLabel { text: "MCP servers" }
      AppComboBox {
        id: mcpBox
        objectName: "task-mcp"
        enabled: page.mcpProfiles
        model: [{ value: "", label: "The agent's own servers" }]
        textRole: "label"
        valueRole: "value"
      }
    }

    ColumnLayout {
      FieldLabel { text: "Runs on" }
      AppComboBox {
        id: providerBox
        objectName: "task-provider"
        enabled: page.routing
        model: [{ value: "", label: "Subscription" }]
        textRole: "label"
        valueRole: "value"
      }
    }
  }

  RowLayout {
    Layout.fillWidth: true
    spacing: 12
    Switch {
      id: worktreeSwitch
      objectName: "task-worktree"
      enabled: page.inRepo
      checked: true
      indicator: Rectangle {
        implicitWidth: 40
        implicitHeight: 20
        x: worktreeSwitch.leftPadding
        y: (worktreeSwitch.height - height) / 2
        radius: 10
        opacity: worktreeSwitch.enabled ? 1 : 0.4
        color: worktreeSwitch.checked && worktreeSwitch.enabled ? theme.accent : theme.selection
        Rectangle {
          x: worktreeSwitch.checked ? parent.width - width - 2 : 2
          y: 2
          width: 16; height: 16; radius: 8
          color: theme.foreground
        }
      }
    }
    Label {
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: page.inRepo ? theme.foreground : theme.muted
      text: page.inRepo
        ? "Work in a separate git worktree and branch, so parallel agents do not collide. Merge it from the Worktrees page."
        : "Separate worktree: only for folders in a git repository."
    }
  }

  Label {
    visible: !!page.error
    Layout.fillWidth: true
    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
    color: theme.urgent
    text: page.error
  }

  // When to run it, while Schedule… is open.
  Rectangle {
    objectName: "task-schedule"
    visible: page.scheduleOpen
    Layout.fillWidth: true
    implicitHeight: scheduleRow.implicitHeight + 24
    radius: 6
    color: Qt.alpha(theme.surface, 0.6)
    border.color: theme.selection
    GridLayout {
      id: scheduleRow
      anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 12 }
      columns: page.width >= 760 ? 3 : 1
      columnSpacing: 12
      rowSpacing: 8
      TextField {
        id: whenField
        objectName: "task-when"
        Layout.preferredWidth: 220
        implicitHeight: 32
        placeholderText: "weekdays 09:00"
        placeholderTextColor: theme.muted
        color: theme.foreground
        selectionColor: theme.selection
        background: Rectangle {
          radius: 4
          color: theme.surface
          border.color: whenField.text && page.whenCheck.error ? theme.urgent : whenField.activeFocus ? theme.accent : theme.selection
        }
        Keys.onReturnPressed: page.addSchedule()
        Keys.onEnterPressed: page.addSchedule()
      }
      Label {
        objectName: "task-when-check"
        Layout.fillWidth: true
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        font.pixelSize: 12
        color: whenField.text && page.whenCheck.error ? theme.urgent : theme.muted
        text: !whenField.text ? "daily 09:00, weekdays 09:00, mon,thu 18:30, every 6h, every 30m (local time)"
              : page.whenCheck.error ? page.whenCheck.error
              : thenText.text.trim() !== "" ? "A \"Then…\" follow-up cannot be scheduled; a recipe can."
              : page.whenCheck.text + ", first on " + page.whenCheck.next + ". Each time it joins the queue like any task."
      }
      AppButton {
        objectName: "task-schedule-save"
        text: "Schedule"
        primary: true
        enabled: page.canSchedule
        onClicked: page.addSchedule()
      }
    }
  }

  // The note beside the buttons, or above them in a narrow window.
  GridLayout {
    id: noteAndButtons
    Layout.fillWidth: true
    columns: page.width >= 760 ? 2 : 1
    columnSpacing: 16
    rowSpacing: 8
    Label {
      Layout.fillWidth: true
      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
      color: theme.muted
      font.pixelSize: 12
      text: page.chained
        ? "A chain is queued: each step starts once the one before finishes, and one that stops or fails holds the rest. A step that waits for you pauses the chain."
        : "Launch opens it in a new terminal window now; Add to queue waits for a free agent slot. The first time an agent works in a folder it asks whether to trust it; answer there."
    }
    // Right-aligned, and wrapping onto a second line when the page is too
    // narrow for all four (listed last first: right to left).
    Flow {
      id: buttonFlow
      objectName: "task-buttons"
      // All on one line where there is room (a Flow reports no such width
      // of its own); the layout may give it less, and it wraps.
      readonly property real oneLine: {
        let w = 0, n = 0
        for (const c of children) if (c.visible) { w += c.implicitWidth; n++ }
        return w + Math.max(0, n - 1) * spacing
      }
      // Beside the note it keeps its one line and the note takes the rest;
      // under it, it spans the page and wraps only if it must.
      Layout.fillWidth: noteAndButtons.columns === 1
      Layout.preferredWidth: oneLine
      Layout.minimumWidth: 0
      layoutDirection: Qt.RightToLeft
      spacing: 8
      AppButton {
        objectName: "task-launch"
        text: page.chained ? "Start chain" : "Launch"
        primary: true
        enabled: page.ready
        onClicked: page.launch()
      }
      AppButton {
        objectName: "task-queue"
        visible: !page.chained
        text: "Add to queue"
        enabled: page.ready
        onClicked: page.addToQueue()
      }
      AppButton {
        objectName: "task-schedule-open"
        text: page.scheduleOpen ? "No schedule" : "Schedule…"
        quiet: true
        onClicked: {
          page.scheduleOpen = !page.scheduleOpen
          if (page.scheduleOpen) whenField.forceActiveFocus()
        }
        ToolTip.visible: hovered
        ToolTip.delay: 500
        ToolTip.text: "Queue this task on a timetable: daily, on weekdays, or every few hours."
      }
      AppButton {
        objectName: "task-as-fleet"
        text: "Run as fleet…"
        quiet: true
        onClicked: page.asFleet(prompt.text, folder.text)
        ToolTip.visible: hovered
        ToolTip.delay: 500
        ToolTip.text: "A scout, an architect, builders and reviewers, with a plan you approve first. Carries the text and folder over."
      }
    }
  }

  Item { Layout.fillHeight: true }

  FolderDialog {
    id: folderDialog
    title: "Folder for the agent"
    onAccepted: folder.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\//, ""))
  }
}
