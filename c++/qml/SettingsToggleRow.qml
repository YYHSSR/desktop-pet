import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string settingKey
    property string title
    property string description
    property bool defaultValue: false
    property bool checked: false
    Layout.fillWidth: true
    implicitHeight: Math.max(62, copy.implicitHeight + 20)

    function refresh() {
        checked = settingKey === "__autostart__"
            ? configManager.autostartEnabled()
            : Boolean(configManager.value(settingKey, defaultValue))
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey) {
                root.checked = Boolean(val)
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
        anchors.right: toggle.left
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

    Rectangle {
        id: toggle
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: 38
        height: 22
        radius: 11
        color: root.checked ? "#0a84ff" : "#dedede"

        Rectangle {
            width: 18
            height: 18
            radius: 9
            y: 2
            x: root.checked ? 18 : 2
            color: "white"
            border.color: "#c9c9c9"
            Behavior on x { NumberAnimation { duration: 100 } }
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                root.checked = !root.checked
                if (root.settingKey === "__autostart__") {
                    if (!configManager.setAutostartEnabled(root.checked)) {
                        root.checked = !root.checked
                    }
                } else {
                    configManager.setValue(root.settingKey, root.checked)
                }
            }
        }
    }
}
