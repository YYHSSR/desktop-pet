import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 动作按钮设置项（Python SettingRow + QPushButton）。
Item {
    id: root
    property string title
    property string description
    property string buttonText: ""
    property int buttonWidth: 72

    signal activated()

    Layout.fillWidth: true
    implicitHeight: Math.max(62, copy.implicitHeight + 20)

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
        anchors.right: actionButton.left
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

    Button {
        id: actionButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: root.buttonText
        implicitWidth: root.buttonWidth
        font.pixelSize: 12
        enabled: root.enabled
        onClicked: root.activated()
    }
}
