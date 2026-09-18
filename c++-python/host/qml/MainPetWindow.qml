import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQml
import DesktopPet

Window {
    id: rootWindow
    visible: true
    title: "desktop-pet 桌宠"

    function openSettings() {
        settingsWindow.show()
        settingsWindow.raise()
        settingsWindow.requestActivate()
    }

    // 基础尺寸 (对齐 640x360 原始 WebM 比例)
    width: 640 * ((typeof petController !== 'undefined' && petController) ? petController.scale : 0.72)
    height: 360 * ((typeof petController !== 'undefined' && petController) ? petController.scale : 0.72)
    x: (typeof petController !== 'undefined' && petController) ? petController.posX : 500
    y: (typeof petController !== 'undefined' && petController) ? petController.posY : 300

    // 透明置顶无边框
    flags: Qt.FramelessWindowHint | Qt.Tool
           | ((typeof configManager !== 'undefined' && configManager.onTop)
              ? Qt.WindowStaysOnTopHint : 0)
    color: "transparent"

    opacity: ((typeof petController !== 'undefined' && petController) && !petController.windowVisible)
             ? 0.0 : ((typeof configManager !== 'undefined') ? configManager.petOpacity / 100.0 : 1.0)

    // 设置中心弹窗实例
    ModernSettingsView {
        id: settingsWindow
        visible: false
    }


    Item {
        id: container
        anchors.fill: parent

        // Q 弹形变与镜像变换组合
        transform: [
            Scale {
                origin.x: container.width * 0.5
                origin.y: container.height * 0.8
                xScale: (typeof petController !== 'undefined' && petController) ? ((petController.facingRight ? -1.0 : 1.0) * petController.squashX) : 1.0
                yScale: (typeof petController !== 'undefined' && petController) ? petController.squashY : 1.0
            }
        ]

        // 直接绘制 C++ RGBA 帧，避免每帧变更 image:// URL 所造成的
        // QML 图片加载、缩放与纹理重建抖动。
        PetFrameItem {
            id: petImage
            anchors.fill: parent
            controller: (typeof petController !== 'undefined') ? petController : null
        }

        // 拖拽手势交互区 (支持左右键复合交互)
        SpringDragArea {
            anchors.fill: parent
            onPointerPressed: (screenX, screenY, button, buttons, modifiers) => {
                if (typeof petController !== 'undefined' && petController) {
                    petController.onPointerPressed(screenX, screenY, button, buttons, modifiers)
                }
            }
            onPointerMoved: (screenX, screenY, buttons) => {
                if (typeof petController !== 'undefined' && petController) {
                    petController.onPointerMoved(screenX, screenY, buttons)
                }
            }
            onPointerReleased: (screenX, screenY, button, buttons) => {
                if (typeof petController !== 'undefined' && petController) {
                    petController.onPointerReleased(screenX, screenY, button, buttons)
                }
            }
            onRightClicked: (localX, localY) => {
                if (typeof petController !== 'undefined' && petController) {
                    petController.showContextMenu()
                }
            }
        }
    }

    // 动态跟随对话气泡 (置于翻转 container 外部，文本永远保持正向可读)
    SpeechBubble {
        id: speechBubble
        anchors.top: (typeof petController !== 'undefined' && petController && !petController.speechBelow)
                     ? parent.top : undefined
        anchors.bottom: (typeof petController !== 'undefined' && petController && petController.speechBelow)
                        ? parent.bottom : undefined
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        z: 100
        text: (typeof petController !== 'undefined' && petController) ? petController.speechText : ""
        imageSource: (typeof petController !== 'undefined' && petController) ? petController.speechImage : ""
        styleId: (typeof petController !== 'undefined' && petController) ? petController.speechStyle : "classic_top"
        placement: (typeof petController !== 'undefined' && petController) ? petController.speechPlacement : "top"
        below: (typeof petController !== 'undefined' && petController) ? petController.speechBelow : false
        visible: (typeof petController !== 'undefined' && petController) ? petController.speechVisible : false
    }
}
