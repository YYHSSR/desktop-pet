import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 单行文本设置项（对齐 Python SettingRow + QLineEdit 的形态）。
// mode 为 "agentThinking" 时读写 agent_link.thinking_texts.<agentKey>。
Item {
    id: root
    property string settingKey
    property string title
    property string description
    property string defaultValue: ""
    property string placeholderText: ""
    property int maxLength: 500
    property int fieldWidth: 240
    property string mode: ""
    property string agentKey: ""

    Layout.fillWidth: true
    implicitHeight: Math.max(62, copy.implicitHeight + 20)

    function refresh() {
        field.text = (root.mode === "agentThinking")
            ? configManager.agentThinkingText(root.agentKey)
            : String(configManager.value(settingKey, defaultValue) || "")
    }

    function commit() {
        if (root.mode === "agentThinking") {
            configManager.setAgentThinkingText(root.agentKey, field.text.trim())
        } else {
            configManager.setValue(root.settingKey, field.text)
        }
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (root.mode === "agentThinking") return
            if (key === root.settingKey && !field.activeFocus) {
                field.text = String(val || "")
            }
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
        anchors.right: field.left
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

    TextField {
        id: field
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: root.fieldWidth
        font.pixelSize: 12
        maximumLength: root.maxLength
        placeholderText: root.placeholderText
        onEditingFinished: root.commit()
    }
}
