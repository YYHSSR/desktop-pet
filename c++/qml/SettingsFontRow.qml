import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// UI 字体设置项（Python ModernSelect + 惰性枚举系统字体族）。
// Windows 字体较多，首次枚举会明显拖慢打开速度，因此仅在用户展开下拉时才加载。
Item {
    id: root
    property string settingKey
    property string title
    property string description
    property string defaultValue: "system"

    Layout.fillWidth: true
    implicitHeight: Math.max(66, copy.implicitHeight + 20)

    property bool fontsPopulated: false

    ListModel { id: fontModel }

    function currentValue() {
        var index = combo.currentIndex
        return index >= 0 ? fontModel.get(index).value : root.defaultValue
    }

    function hasValue(value) {
        for (var i = 0; i < fontModel.count; ++i) {
            if (fontModel.get(i).value === value) return true
        }
        return false
    }

    function selectValue(value) {
        for (var i = 0; i < fontModel.count; ++i) {
            if (fontModel.get(i).value === value) {
                combo.currentIndex = i
                return
            }
        }
        combo.currentIndex = 0
    }

    function refresh() {
        var stored = String(configManager.value(root.settingKey, root.defaultValue) || "system")
        fontModel.clear()
        fontModel.append({ "label": "系统默认", "value": "system" })
        // 保留当前配置值无需枚举字体库，避免用户未展开选择器时被静默重置为系统默认
        if (stored !== "system") fontModel.append({ "label": stored, "value": stored })
        root.selectValue(stored)
    }

    function populateFonts() {
        if (root.fontsPopulated) return
        root.fontsPopulated = true
        var current = root.currentValue()
        var families = configManager.systemFontFamilies()
        for (var i = 0; i < families.length; ++i) {
            if (!root.hasValue(families[i])) {
                fontModel.append({ "label": families[i], "value": families[i] })
            }
        }
        root.selectValue(current)
    }

    Component.onCompleted: refresh()

    Connections {
        target: (typeof configManager !== 'undefined') ? configManager : null
        function onValueChanged(key, val) {
            if (key === root.settingKey) root.refresh()
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
        anchors.right: combo.left
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

    ComboBox {
        id: combo
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: 172
        font.pixelSize: 12
        model: fontModel
        textRole: "label"
        valueRole: "value"
        onPressedChanged: if (pressed) root.populateFonts()
        onActivated: configManager.setValue(root.settingKey, root.currentValue())
    }
}
