import QtQuick
import QtQuick.Controls

Item {
    id: bubbleRoot

    property string text: ""
    property string imageSource: ""
    // Python speech_bubble.py 的 5 套气泡方案
    property string styleId: "classic_top"
    // 相对桌宠的水平位置（top / top_left / top_right，由控制器按屏幕边界选择）
    property string placement: "top"
    property bool below: false

    readonly property bool showingImage: imageSource.length > 0
    readonly property var presets: ({
        "classic_top":   { bg: "#fffaf0", border: "#efc261", fg: "#403725", radius: 14, shadow: "#6b542b" },
        "paper_left":    { bg: "#ffffff", border: "#dce1e7", fg: "#252a32", radius: 11, shadow: "#374151" },
        "glass_right":   { bg: "#292d36", border: "#4d5360", fg: "#f7f8fb", radius: 18, shadow: "#111318" },
        "soft_blue_top": { bg: "#eef6ff", border: "#a9c9ef", fg: "#24466f", radius: 16, shadow: "#315f91" },
        "breath_bubble": { bg: "#fbfeff", border: "#0e5968", fg: "#23444d", radius: 18, shadow: "#0e5968" }
    })
    readonly property var preset: presets[styleId] !== undefined ? presets[styleId] : presets["classic_top"]
    // breath_bubble：有机水泡造型近似——高圆角 + 两个分离小泡 + 高光（Python :657-766 的简化）
    readonly property bool isBreath: styleId === "breath_bubble"

    implicitWidth: showingImage
        ? Math.min(252, Math.max(96, imageItem.paintedWidth + 32))
        : Math.min(320, label.implicitWidth + 32)
    implicitHeight: showingImage
        ? Math.min(172, imageItem.paintedHeight + 24)
        : Math.min(176, label.implicitHeight + 24)
    opacity: visible ? 1.0 : 0.0

    Behavior on opacity {
        NumberAnimation { duration: 250; easing.type: Easing.OutCubic }
    }

    // Python bubble_rect_for_anchor：top 居中 / top_left 靠左 / top_right 靠右
    x: placement === "top_left" ? 4
       : placement === "top_right" ? parent.width - width - 4
       : (parent.width - width) / 2

    // 指向桌宠的气泡尾巴（Python _update_surface_geometry 的窗口内近似）：
    // 气泡在桌宠上方，尾巴贴在下缘，随 placement 左右偏移
    Rectangle {
        visible: !bubbleRoot.below
        width: 14
        height: 14
        radius: 2
        rotation: 45
        x: bubbleRoot.placement === "top_left" ? 14
           : bubbleRoot.placement === "top_right" ? bubbleRoot.width - width - 14
           : (bubbleRoot.width - width) / 2
        y: bubbleRoot.height - height / 2 - 1
        color: bubbleRoot.preset.bg
        border.color: bubbleRoot.preset.border
        border.width: 1
    }

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: bubbleRoot.preset.radius
        color: bubbleRoot.preset.bg
        border.color: bubbleRoot.preset.border
        border.width: 1

        // 半透明软阴影（Python _shadow_layers 的简化等效）
        Rectangle {
            z: -1
            anchors.fill: parent
            anchors.margins: -2
            radius: bubbleRoot.preset.radius + 2
            color: bubbleRoot.preset.shadow
            opacity: 0.10
        }

        // breath_bubble 的分离小泡与高光（造型近似）
        Rectangle {
            visible: bubbleRoot.isBreath
            width: 12; height: 12; radius: 6
            x: -14; y: bubbleRoot.height * 0.55
            color: bubbleRoot.preset.bg
            border.color: bubbleRoot.preset.border
            border.width: 1
        }
        Rectangle {
            visible: bubbleRoot.isBreath
            width: 7; height: 7; radius: 3
            x: -8; y: bubbleRoot.height * 0.28
            color: bubbleRoot.preset.bg
            border.color: bubbleRoot.preset.border
            border.width: 1
        }
        Rectangle {
            visible: bubbleRoot.isBreath
            width: parent.width * 0.4; height: 6; radius: 3
            x: parent.width * 0.14; y: 7
            color: "#ffffff"
            opacity: 0.55
        }

        Label {
            id: label
            // 关键绑定：气泡文本来自根属性（缺失时 Label 恒为空 → 气泡缩成 32px 空壳）
            text: bubbleRoot.text
            visible: !bubbleRoot.showingImage
            anchors.centerIn: parent
            width: Math.min(280, implicitWidth)
            wrapMode: Text.Wrap
            // Python bubble_max_lines：最多 6 行，超出省略（完整分页暂以行数上限近似）
            maximumLineCount: 6
            elide: Text.ElideRight
            font.pixelSize: 13
            color: bubbleRoot.preset.fg
            horizontalAlignment: Text.AlignHCenter
        }

        // Python show_image：220x140 KeepAspectRatio，圆角裁剪于气泡内
        Image {
            id: imageItem
            visible: bubbleRoot.showingImage
            anchors.centerIn: parent
            anchors.margins: 10
            source: bubbleRoot.showingImage ? bubbleRoot.imageSource : ""
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false
            sourceSize.width: 220
            sourceSize.height: 140
        }
    }
}
