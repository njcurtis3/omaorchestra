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
  // A ComboBox in theme colours (the Basic style's own is grey), popup included.
  component ThemedComboBox: ComboBox {
    // Wide enough for the longest choice, so none is cut off.
    implicitContentWidthPolicy: ComboBox.WidestTextWhenCompleted
    palette.button: theme.surface
    palette.buttonText: theme.foreground
    palette.base: theme.surface
    palette.text: theme.foreground
    palette.window: theme.surface
    palette.windowText: theme.foreground
    palette.highlight: theme.selection
    palette.highlightedText: theme.foreground
    palette.mid: theme.selection
    palette.dark: theme.muted
  }
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
  Button {
    objectName: "task-then-open"
    visible: !page.thenOpen && !page.recipe
    text: "Then…"
    flat: true
    onClicked: { page.thenOpen = true; thenText.forceActiveFocus() }
    contentItem: Label { text: parent.text; color: theme.accent }
    background: null
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
      Layout.fillWidth: true
      color: theme.foreground
      selectionColor: theme.selection
      placeholderText: "~/code/project"
      placeholderTextColor: theme.muted
      background: Rectangle { radius: 4; color: theme.surface; border.color: folder.text && !page.folderOk ? theme.urgent : folder.activeFocus ? theme.accent : theme.selection }
    }
    Button {
      text: "Browse…"
      flat: true
      onClicked: folderDialog.open()
      contentItem: Label { text: parent.text; color: theme.foreground; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; color: parent.hovered ? theme.selection : "transparent"; border.color: theme.selection }
    }
  }
  Label {
    visible: !!folder.text && !page.folderOk
    color: theme.urgent
    text: "That folder does not exist."
  }
  Flow {
    Layout.fillWidth: true
    spacing: 6
    Repeater {
      id: recentList
      model: []
      delegate: Button {
        required property string modelData
        text: modelData.replace(/^\/home\/[^/]+/, "~")
        flat: true
        onClicked: folder.text = modelData
        contentItem: Label { text: parent.text; color: folder.text === modelData ? theme.foreground : theme.muted; font.pixelSize: 12 }
        background: Rectangle { radius: 4; color: folder.text === modelData ? theme.selection : parent.hovered ? Qt.alpha(theme.selection, 0.5) : "transparent"; border.color: theme.selection }
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
      ThemedComboBox {
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

    CheckBox {
      id: rememberBox
      objectName: "task-remember-model"
      Layout.alignment: Qt.AlignBottom
      enabled: page.folderOk && !providerBox.currentValue && (agentBox.currentValue || "claude") === "claude"
      text: "Remember for this folder"
      contentItem: Label { text: rememberBox.text; color: rememberBox.enabled ? theme.foreground : theme.muted; leftPadding: rememberBox.indicator.width + 6 }
    }

    ColumnLayout {
      FieldLabel { text: "Recipe" }
      ThemedComboBox {
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
      ThemedComboBox {
        id: permissionBox
        objectName: "task-permissions"
        model: sessions.permissionChoices
        textRole: "label"
        valueRole: "value"
      }
    }

    ColumnLayout {
      FieldLabel { text: "Agent" }
      ThemedComboBox {
        id: agentBox
        objectName: "task-agent"
        model: [{ value: "claude", label: "Claude Code", routing: true, mcpProfile: true }]
        textRole: "label"
        valueRole: "value"
      }
    }

    ColumnLayout {
      FieldLabel { text: "MCP servers" }
      ThemedComboBox {
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
      ThemedComboBox {
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
      wrapMode: Text.Wrap
      color: page.inRepo ? theme.foreground : theme.muted
      text: page.inRepo
        ? "Work in a separate git worktree and branch, so parallel agents do not collide. Merge it from the Worktrees page."
        : "Separate worktree: only for folders in a git repository."
    }
  }

  Label {
    visible: !!page.error
    Layout.fillWidth: true
    wrapMode: Text.Wrap
    color: theme.urgent
    text: page.error
  }

  RowLayout {
    Layout.fillWidth: true
    Label {
      Layout.fillWidth: true
      wrapMode: Text.Wrap
      color: theme.muted
      font.pixelSize: 12
      text: page.chained
        ? "A chain is queued: each step starts once the one before finishes, and one that stops or fails holds the rest. A step that waits for you pauses the chain."
        : "Launch opens it in a new terminal window now; Add to queue waits for a free agent slot. The first time an agent works in a folder it asks whether to trust it; answer there."
    }
    Button {
      objectName: "task-queue"
      visible: !page.chained
      text: "Add to queue"
      flat: true
      enabled: page.ready
      onClicked: page.addToQueue()
      contentItem: Label { text: parent.text; color: parent.enabled ? theme.foreground : theme.muted; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; implicitWidth: 120; color: parent.hovered && parent.enabled ? theme.selection : "transparent"; border.color: theme.selection }
    }
    Button {
      objectName: "task-launch"
      text: page.chained ? "Start chain" : "Launch"
      enabled: page.ready
      onClicked: page.launch()
      contentItem: Label { text: parent.text; color: parent.enabled ? theme.background : theme.muted; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; implicitWidth: 120; color: parent.enabled ? theme.accent : "transparent"; border.color: theme.selection }
    }
  }

  Item { Layout.fillHeight: true }

  FolderDialog {
    id: folderDialog
    title: "Folder for the agent"
    onAccepted: folder.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\//, ""))
  }
}
