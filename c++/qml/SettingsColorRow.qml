import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// 颜色设置项（Python ColorPicker：#RRGGBB 输入框 + 色块按钮 + 原生取色面板）。
Item {
    id: root
    property string settingKey
    property string title
    property string description
    property string defaultValue: "#ffffff"

    Layout.fillWidth: true
    implicitHeight: Math.max(62, copy.implicitHeight + 20)

    function validColor() {
        var hex = field.text.trim()
        return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : root.defaultValue
    }

    function toHex(color) {
        var text = String(color) // "#rrggbb" 或 "#aarrggbb"
        if (text.length === 9) text = "#" + text.substring(3)
        return text.toLowerCase()
    }

    function refresh() {
        field.text = String(configManager.value(root.settingKey, root.defaultValue) || root.defaultValue)
    }

    function commit() {
        configManager.setValue(root.settingKey, field.text.trim())
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey && !field.activeFocus) field.text = String(val || "")
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
        spacing: 6

        TextField {
            id: field
            implicitWidth: 96
            font.pixelSize: 12
            onEditingFinished: root.commit()
        }

        Rectangle {
            Layout.preferredWidth: 34
            Layout.preferredHeight: 26
            radius: 7
            border.color: "#aeb3b8"
            border.width: 1
            color: root.validColor()

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    colorDialog.selectedColor = root.validColor()
                    colorDialog.open()
                }
            }
        }
    }

    ColorDialog {
        id: colorDialog
        onAccepted: {
            field.text = root.toHex(selectedColor)
            root.commit()
        }
    }
}
