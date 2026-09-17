import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 多行文本设置项（Python SettingRow(..., stacked=True) + QPlainTextEdit）。
// valueIsList 为 true 时，配置值按「每行一条」的字符串数组读写。
Item {
    id: root
    property string settingKey
    property string title
    property string description
    property bool valueIsList: false
    property var defaultValue
    property int areaHeight: 108
    property int lineMaxLength: 120

    Layout.fillWidth: true
    implicitHeight: copyBlock.implicitHeight + 8 + areaHeight + 18

    function listToText(value) {
        if (!value) return ""
        var out = []
        for (var i = 0; i < value.length; ++i) out.push(String(value[i]))
        return out.join("\n")
    }

    function textToList(text) {
        var lines = String(text).split("\n")
        var out = []
        for (var i = 0; i < lines.length; ++i) {
            var line = lines[i].trim()
            if (line.length) out.push(root.lineMaxLength > 0 ? line.substring(0, root.lineMaxLength) : line)
        }
        return out
    }

    function refresh() {
        var stored = configManager.value(root.settingKey, root.defaultValue)
        area.text = root.valueIsList ? listToText(stored) : String(stored || "")
    }

    function commit() {
        if (root.valueIsList) {
            var list = textToList(area.text)
            configManager.setValue(root.settingKey, list.length ? list : root.defaultValue)
        } else {
            configManager.setValue(root.settingKey, area.text)
        }
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey && !area.activeFocus) {
                area.text = root.valueIsList ? root.listToText(val) : String(val || "")
            }
        }
    }

    ColumnLayout {
        id: copyBlock
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: 12
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

    ScrollView {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: copyBlock.bottom
        anchors.topMargin: 8
        height: root.areaHeight
        clip: true

        TextArea {
            id: area
            wrapMode: TextArea.Wrap
            font.pixelSize: 12
            font.family: "Microsoft YaHei UI"
            selectByMouse: true
            onActiveFocusChanged: if (!activeFocus) root.commit()
        }
    }
}
