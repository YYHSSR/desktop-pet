import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// 绝对路径设置项（Python ResourcePathPicker）。
// assetMode 为 "eggAvatar"/"eggImageDir" 时，读写走 resolve/store 归一化，
// 使内置 assets 内的路径始终保持可移植的相对形式。
Item {
    id: root
    property string settingKey
    property string title
    property string description
    property bool directory: false
    property string assetMode: ""
    property string fileFilter: "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)"
    property int controlWidth: 340

    Layout.fillWidth: true
    implicitHeight: Math.max(62, copy.implicitHeight + 20)

    function fallbackPath() {
        if (root.assetMode === "eggAvatar") return configManager.defaultEasterEggAvatar()
        if (root.assetMode === "eggImageDir") return configManager.defaultEasterEggImageDir()
        return ""
    }

    function refresh() {
        var stored = String(configManager.value(root.settingKey, "") || "")
        field.text = (root.assetMode === "")
            ? stored
            : configManager.resolveAssetPath(stored, root.fallbackPath())
    }

    function commit() {
        var text = field.text.trim()
        if (root.assetMode === "") configManager.setValue(root.settingKey, text)
        else configManager.setValue(root.settingKey, configManager.storeAssetPath(text, root.fallbackPath()))
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey && !field.activeFocus) root.refresh()
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: "#eceff2"
    }

    ColumnLayout {
        id: copy
        anchors.left: parent.left
        anchors.right: control.left
        anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Label {
            Layout.fillWidth: true
            text: root.title
            color: "#202124"
            font.pixelSize: 13
            font.family: "Microsoft YaHei UI"
        }
        Label {
            Layout.fillWidth: true
            text: root.description
            color: "#6b7177"
            font.pixelSize: 11
            font.family: "Microsoft YaHei UI"
            wrapMode: Text.WordWrap
        }
    }

    RowLayout {
        id: control
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: root.controlWidth
        spacing: 6

        TextField {
            id: field
            Layout.fillWidth: true
            font.pixelSize: 12
            onEditingFinished: root.commit()
        }
        Button {
            text: "选择…"
            implicitWidth: 66
            font.pixelSize: 12
            onClicked: root.directory ? folderPicker.open() : filePicker.open()
        }
    }

    FileDialog {
        id: filePicker
        title: "选择文件"
        nameFilters: [root.fileFilter, "所有文件 (*)"]
        onAccepted: { field.text = configManager.localPath(selectedFile); root.commit() }
    }

    FolderDialog {
        id: folderPicker
        title: "选择文件夹"
        onAccepted: { field.text = configManager.localPath(selectedFolder); root.commit() }
    }
}
