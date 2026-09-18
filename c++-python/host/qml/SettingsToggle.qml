import QtQuick

// 裸开关控件（视觉与 SettingsToggleRow 内的开关完全一致）。
// 只发出 toggled 信号，由使用方决定是否写配置/回滚，便于组合进复杂行。
Rectangle {
    id: root
    property bool checked: false

    signal toggled(bool value)

    implicitWidth: 38
    implicitHeight: 22
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
        onClicked: root.toggled(!root.checked)
    }
}
