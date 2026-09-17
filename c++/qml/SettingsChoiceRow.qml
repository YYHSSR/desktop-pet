import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string settingKey
    property string title
    property string description
    property var labels: []
    property var values: []
    property var defaultValue
    Layout.fillWidth: true
    implicitHeight: 66

    function refresh() {
        selectStored()
    }

    function selectStored() {
        var stored = configManager.value(settingKey, defaultValue)
        var index = values.indexOf(stored)
        combo.currentIndex = index >= 0 ? index : 0
    }
    Component.onCompleted: selectStored()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey) {
                var idx = root.values.indexOf(val)
                if (idx >= 0) combo.currentIndex = idx
            }
        }
    }

    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: "#eceff2" }
    ColumnLayout {
        anchors.left: parent.left
        anchors.right: combo.left
        anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Label { Layout.fillWidth: true; text: root.title; color: "#202124"; font.pixelSize: 13; font.family: "Microsoft YaHei UI" }
        Label { Layout.fillWidth: true; text: root.description; color: "#6b7177"; font.pixelSize: 11; font.family: "Microsoft YaHei UI"; wrapMode: Text.WordWrap }
    }
    ComboBox {
        id: combo
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: 142
        model: root.labels
        font.pixelSize: 12
        onActivated: configManager.setValue(root.settingKey, root.values[currentIndex])
    }
}
