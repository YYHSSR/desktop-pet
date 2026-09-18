import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    property string title: ""
    default property alias rows: rowsColumn.data
    Layout.fillWidth: true
    implicitHeight: cardColumn.implicitHeight + 28
    radius: 12
    color: "#ffffff"
    border.color: "#dfe3e8"
    border.width: 1

    ColumnLayout {
        id: cardColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 14
        spacing: 0

        Label {
            visible: root.title.length > 0
            text: root.title
            color: "#202124"
            font.pixelSize: 13
            font.bold: true
            font.family: "Microsoft YaHei UI"
            Layout.bottomMargin: visible ? 4 : 0
        }

        ColumnLayout {
            id: rowsColumn
            Layout.fillWidth: true
            spacing: 0
        }
    }
}
