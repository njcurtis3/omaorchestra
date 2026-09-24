import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Model providers: add them, store their API keys (in the system keyring,
// never in a file), test them, and refresh their model lists.
// `providerList` and `theme` come from Python.
ColumnLayout {
  id: page
  spacing: 12

  property var results: ({})   // provider id -> {ok, text} from the last test
  property string message: ""
  property bool refreshing: false
  property string keyFor: ""

  onVisibleChanged: if (visible) providerList.reload()

  Connections {
    target: providerList
    function onTestDone(id, ok, text) {
      const next = Object.assign({}, page.results)
      next[id] = { ok: ok, text: text }
      page.results = next
      providerList.reload()
    }
    function onRefreshDone() { page.refreshing = false }
  }

  component PlainButton: Button {
    flat: true
    contentItem: Label { text: parent.text; color: parent.enabled ? theme.foreground : theme.muted; horizontalAlignment: Text.AlignHCenter }
    background: Rectangle { radius: 4; implicitWidth: 80; color: parent.hovered && parent.enabled ? theme.selection : "transparent"; border.color: theme.selection }
  }

  RowLayout {
    Layout.fillWidth: true
    Label {
      Layout.fillWidth: true
      wrapMode: Text.Wrap
      color: theme.muted
      text: "API providers for models beyond your subscription. Keys go to the system keyring. omaorchestra only lists models and checks keys; your agents do the talking."
    }
    PlainButton {
      text: page.refreshing ? "Refreshing…" : "Refresh models"
      enabled: !page.refreshing && providerList.items.length > 0
      onClicked: { page.refreshing = true; providerList.refreshModels() }
    }
  }

  Label { visible: !!providerList.error; color: theme.urgent; text: providerList.error; wrapMode: Text.Wrap; Layout.fillWidth: true }
  Label { visible: !!page.message; color: theme.urgent; text: page.message; wrapMode: Text.Wrap; Layout.fillWidth: true }

  // ---------------------------------------------------------- Add
  RowLayout {
    Layout.fillWidth: true
    spacing: 8
    ComboBox {
      id: kindBox
      objectName: "provider-kind"
      Layout.preferredWidth: 180
      model: providerList.kinds
      textRole: "label"
      valueRole: "value"
      palette.button: theme.surface
      palette.buttonText: theme.foreground
      palette.window: theme.surface
      palette.windowText: theme.foreground
      palette.highlight: theme.selection
      palette.highlightedText: theme.foreground
    }
    TextField {
      id: idField
      objectName: "provider-id"
      Layout.preferredWidth: 140
      placeholderText: "id (optional)"
      placeholderTextColor: theme.muted
      color: theme.foreground
      background: Rectangle { radius: 4; color: theme.surface; border.color: theme.selection }
    }
    TextField {
      id: urlField
      objectName: "provider-url"
      Layout.fillWidth: true
      placeholderText: kindBox.currentIndex >= 0 ? providerList.kinds[kindBox.currentIndex].url : ""
      placeholderTextColor: theme.muted
      color: theme.foreground
      background: Rectangle { radius: 4; color: theme.surface; border.color: theme.selection }
    }
    PlainButton {
      objectName: "provider-add"
      text: "Add"
      onClicked: {
        const r = providerList.add(kindBox.currentValue, idField.text.trim(), urlField.text.trim())
        page.message = r.error || ""
        if (!r.error) { idField.text = ""; urlField.text = "" }
      }
    }
  }

  Label {
    visible: providerList.items.length === 0
    Layout.fillWidth: true
    Layout.topMargin: 24
    horizontalAlignment: Text.AlignHCenter
    color: theme.muted
    text: "No providers yet."
  }

  ListView {
    Layout.fillWidth: true
    Layout.fillHeight: true
    clip: true
    spacing: 6
    model: providerList.items
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Rectangle {
      id: row
      required property var modelData
      objectName: "provider-" + modelData.id
      readonly property var result: page.results[modelData.id]
      width: ListView.view.width
      height: rowContent.implicitHeight + 20
      radius: 6
      color: theme.surface

      RowLayout {
        id: rowContent
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
        spacing: 14

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 3
          RowLayout {
            spacing: 10
            Label { text: row.modelData.id; color: theme.foreground; font.bold: true }
            Label { text: row.modelData.label; color: theme.muted }
          }
          Label { text: row.modelData.base_url; color: theme.muted; font.pixelSize: 12 }
          Label {
            color: theme.muted
            font.pixelSize: 12
            text: (row.modelData.needsKey ? (row.modelData.keyStored ? "key stored" : "no key yet") : "no key needed")
              + "   ·   " + (row.modelData.modelCount ? row.modelData.modelCount + " models" : "models not fetched")
          }
          Label {
            objectName: "provider-result-" + row.modelData.id
            visible: !!row.result || !!row.modelData.lastError
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.pixelSize: 12
            color: row.result ? (row.result.ok ? theme.accent : theme.urgent) : theme.urgent
            text: row.result ? row.result.text : row.modelData.lastError
          }
        }

        Row {
          Layout.alignment: Qt.AlignTop
          spacing: 8
          PlainButton { text: "Key…"; visible: row.modelData.needsKey; onClicked: { page.keyFor = row.modelData.id; keyField.text = ""; keyDialog.open() } }
          PlainButton { objectName: "provider-test-" + row.modelData.id; text: "Test"; onClicked: providerList.test(row.modelData.id) }
          PlainButton { text: "Remove"; onClicked: { page.message = providerList.remove(row.modelData.id) } }
        }
      }
    }
  }

  Dialog {
    id: keyDialog
    anchors.centerIn: Overlay.overlay
    modal: true
    title: "API key for " + page.keyFor
    standardButtons: Dialog.Cancel | Dialog.Save
    onAccepted: { page.message = providerList.setKey(page.keyFor, keyField.text); keyField.text = "" }
    onRejected: keyField.text = ""
    background: Rectangle { color: theme.surface; radius: 8; border.color: theme.selection }
    header: Label { text: keyDialog.title; padding: 16; color: theme.foreground; font.bold: true }
    contentItem: ColumnLayout {
      Label { text: "Stored in the system keyring, never in a file."; color: theme.muted }
      TextField {
        id: keyField
        Layout.preferredWidth: 420
        echoMode: TextInput.Password
        color: theme.foreground
        background: Rectangle { radius: 4; color: theme.background; border.color: theme.selection }
      }
    }
  }
}
