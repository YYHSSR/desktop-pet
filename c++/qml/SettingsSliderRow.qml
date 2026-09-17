import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string settingKey
    property string title
    property string description
    property real defaultValue: 0
    property real from: 0
    property real to: 1
    property real stepSize: 0.05
    property real storageScale: 1
    property int decimals: 0
    property string suffix: ""
    Layout.fillWidth: true
    implicitHeight: 72

    function refresh() {
        control.value = Number(configManager.value(settingKey, defaultValue)) / storageScale
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey) {
                control.value = Number(val) / root.storageScale
            }
        }
    }

    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: "#eceff2" }
    ColumnLayout {
        anchors.left: parent.left
        anchors.right: control.left
        anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Label { Layout.fillWidth: true; text: root.title; color: "#202124"; font.pixelSize: 13; font.family: "Microsoft YaHei UI" }
        Label { Layout.fillWidth: true; text: root.description; color: "#6b7177"; font.pixelSize: 11; font.family: "Microsoft YaHei UI"; wrapMode: Text.WordWrap }
    }
    RowLayout {
        id: control
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        property alias value: slider.value
        spacing: 7
        Slider {
            id: slider
            implicitWidth: 145
            from: root.from
            to: root.to
            stepSize: root.stepSize
            onMoved: configManager.setValue(root.settingKey, value * root.storageScale)
        }
        Label {
            Layout.preferredWidth: 64
            horizontalAlignment: Text.AlignRight
            text: Number(slider.value).toFixed(root.decimals) + root.suffix
            color: "#41464c"
            font.pixelSize: 12
        }
    }
}
