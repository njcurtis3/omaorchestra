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

  function reset() {
    prompt.text = ""
    rememberBox.checked = false
    error = ""
    prompt.forceActiveFocus()
  }
  function addToQueue() {
    if (!ready) return
    if (rememberBox.checked && !providerBox.currentValue) sessions.rememberModel(folder.text, modelBox.value)
    const result = queue.add(prompt.text, folder.text, modelBox.value, permissionBox.currentValue,
                             page.inRepo && worktreeSwitch.checked, false, providerBox.currentValue || "",
                             mcpBox.currentValue || "")
    if (result.error) { error = result.error; return }
    reset()
    queued()
  }
  function launch() {
    if (!ready) return
    if (rememberBox.checked && !providerBox.currentValue) sessions.rememberModel(folder.text, modelBox.value)
    const result = sessions.launch(prompt.text, folder.text, modelBox.value, permissionBox.currentValue,
                                   page.inRepo && worktreeSwitch.checked, providerBox.currentValue || "",
                                   mcpBox.currentValue || "")
    if (result.error) { error = result.error; return }
    const id = result.id
    reset()
    launched(id)
  }

  onVisibleChanged: if (visible) {
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
        model: sessions.modelChoices(folder.text, providerBox.currentValue || "")
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
      enabled: page.folderOk && !providerBox.currentValue
      text: "Remember for this folder"
      contentItem: Label { text: rememberBox.text; color: rememberBox.enabled ? theme.foreground : theme.muted; leftPadding: rememberBox.indicator.width + 6 }
    }

    ColumnLayout {
      FieldLabel { text: "Permissions" }
      ThemedComboBox {
        id: permissionBox
        objectName: "task-permissions"
        Layout.preferredWidth: 300
        model: sessions.permissionChoices
        textRole: "label"
        valueRole: "value"
      }
    }

    ColumnLayout {
      FieldLabel { text: "MCP servers" }
      ThemedComboBox {
        id: mcpBox
        objectName: "task-mcp"
        Layout.preferredWidth: 220
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
        Layout.preferredWidth: 280
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
      text: "Launch opens it in a new terminal window now; Add to queue waits for a free agent slot. The first time an agent works in a folder it asks whether to trust it; answer there."
    }
    Button {
      objectName: "task-queue"
      text: "Add to queue"
      flat: true
      enabled: page.ready
      onClicked: page.addToQueue()
      contentItem: Label { text: parent.text; color: parent.enabled ? theme.foreground : theme.muted; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; implicitWidth: 120; color: parent.hovered && parent.enabled ? theme.selection : "transparent"; border.color: theme.selection }
    }
    Button {
      objectName: "task-launch"
      text: "Launch"
      enabled: page.ready
      onClicked: page.launch()
      contentItem: Label { text: parent.text; color: parent.enabled ? theme.background : theme.muted; horizontalAlignment: Text.AlignHCenter }
      background: Rectangle { radius: 4; implicitWidth: 100; color: parent.enabled ? theme.accent : "transparent"; border.color: theme.selection }
    }
  }

  Item { Layout.fillHeight: true }

  FolderDialog {
    id: folderDialog
    title: "Folder for the agent"
    onAccepted: folder.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\//, ""))
  }
}
