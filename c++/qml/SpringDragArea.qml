import QtQuick

Item {
    id: dragRoot

    signal pointerPressed(real screenX, real screenY, int button, int buttons, int modifiers)
    signal pointerMoved(real screenX, real screenY, int buttons)
    signal pointerReleased(real screenX, real screenY, int button, int buttons)
    signal rightClicked(real localX, real localY)

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: Qt.ArrowCursor

        property point pressPoint: Qt.point(0, 0)
        property bool movedFar: false

        onPressed: (mouse) => {
            pressPoint = Qt.point(mouse.x, mouse.y)
            movedFar = false
            var globalPos = mapToGlobal(mouse.x, mouse.y)
            dragRoot.pointerPressed(globalPos.x, globalPos.y, mouse.button, mouse.buttons, mouse.modifiers)
        }

        onPositionChanged: (mouse) => {
            var dx = mouse.x - pressPoint.x
            var dy = mouse.y - pressPoint.y
            if (Math.hypot(dx, dy) > 6) {
                movedFar = true
            }
            var globalPos = mapToGlobal(mouse.x, mouse.y)
            dragRoot.pointerMoved(globalPos.x, globalPos.y, mouse.buttons)
        }

        onReleased: (mouse) => {
            var globalPos = mapToGlobal(mouse.x, mouse.y)
            dragRoot.pointerReleased(globalPos.x, globalPos.y, mouse.button, mouse.buttons)
        }

        onClicked: (mouse) => {
            if (mouse.button === Qt.RightButton) {
                // 正在拖拽中则抑制菜单弹出
                var isDragging = (typeof petController !== 'undefined' && petController)
                                 ? petController.isDragging : false
                if (!isDragging && !movedFar) {
                    dragRoot.rightClicked(mouse.x, mouse.y)
                }
            }
        }
    }
}
