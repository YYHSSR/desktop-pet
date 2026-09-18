#pragma once

#include <QMenu>
#include <QPoint>
#include <QRect>
#include <QSize>

class QPropertyAnimation;

namespace Pet::UI {

// 对应 Python pet/ui/context_menus/positioning.py：右键菜单定位与滑动过渡。
class MenuPositioning {
public:
    // Python _clamp_menu_rect：把菜单矩形夹到可用屏幕区域内（保持尺寸不变）。
    static QRect clampMenuRect(const QRect& rect, const QRect& available);

    // Python pick_context_menu_position：优先角色右侧、其次左侧、最后重叠最小的角落。
    // 布局方向恒为 LTR（Python 返回值中的 Qt.LayoutDirection 始终为 LeftToRight）。
    static QPoint pickContextMenuPosition(const QRect& petRect, const QSize& menuSize,
                                          int submenuWidth, const QRect& available,
                                          int margin = 10);

    // Python animate_context_menu_to：把已显示的菜单滑动到安全位置，不改变布局。
    // 返回的动画对象父级为菜单，调用方可忽略。
    static QPropertyAnimation* animateContextMenuTo(QMenu* menu, const QPoint& target,
                                                    int durationMs = 140);
};

} // namespace Pet::UI
