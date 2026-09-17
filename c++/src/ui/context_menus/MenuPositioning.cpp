#include "MenuPositioning.hpp"

#include <QEasingCurve>
#include <QPropertyAnimation>
#include <algorithm>

namespace Pet::UI {

QRect MenuPositioning::clampMenuRect(const QRect& rect, const QRect& available) {
    if (available.isEmpty()) {
        return QRect(rect);
    }
    const int x = std::min(std::max(rect.x(), available.left()),
                           std::max(available.left(), available.right() - rect.width() + 1));
    const int y = std::min(std::max(rect.y(), available.top()),
                           std::max(available.top(), available.bottom() - rect.height() + 1));
    return QRect(x, y, rect.width(), rect.height());
}

QPoint MenuPositioning::pickContextMenuPosition(const QRect& petRect, const QSize& menuSize,
                                                int submenuWidth, const QRect& available,
                                                int margin) {
    const int menuWidth = std::max(1, menuSize.width());
    const int menuHeight = std::max(1, menuSize.height());
    const int reservedSubmenu = std::max(0, submenuWidth);

    // 1) 右侧：根菜单整体在角色右侧，且子菜单向右有空间。
    QRect root = clampMenuRect(
        QRect(petRect.right() + margin, petRect.top(), menuWidth, menuHeight), available);
    if (root.left() >= petRect.right() + margin
        && root.right() + reservedSubmenu <= available.right()
        && available.contains(root)) {
        return root.topLeft();
    }

    // 2) 左侧：只按根菜单宽度避让角色。Qt 可自行决定子菜单实际弹出侧，
    //    布局方向仍为 LTR，文字/图标/箭头不会镜像。
    root = clampMenuRect(
        QRect(petRect.left() - margin - menuWidth, petRect.top(), menuWidth, menuHeight),
        available);
    if (root.right() <= petRect.left() - margin && available.contains(root)) {
        return root.topLeft();
    }

    // 3) 远角兜底：按整棵菜单树计算占位，取与角色重叠面积最小的角落。
    const int treeWidth = menuWidth + reservedSubmenu;
    const int rightX = std::max(available.left() + margin,
                                available.right() - treeWidth + 1 - margin);
    const QPoint corners[4] = {
        QPoint(available.left() + margin, available.top() + margin),
        QPoint(rightX, available.top() + margin),
        QPoint(available.left() + margin,
               std::max(available.top() + margin, available.bottom() - menuHeight + 1 - margin)),
        QPoint(rightX,
               std::max(available.top() + margin, available.bottom() - menuHeight + 1 - margin)),
    };

    QPoint best = corners[0];
    int bestArea = -1;
    for (const QPoint& point : corners) {
        const QRect tree(point.x(), point.y(), treeWidth, menuHeight);
        const QRect overlap = tree.intersected(petRect);
        const int area = overlap.width() * overlap.height();
        if (bestArea < 0 || area < bestArea) {
            bestArea = area;
            best = point;
        }
    }
    return best;
}

QPropertyAnimation* MenuPositioning::animateContextMenuTo(QMenu* menu, const QPoint& target,
                                                          int durationMs) {
    if (menu == nullptr || menu->pos() == target) {
        return nullptr;
    }
    auto* animation = new QPropertyAnimation(menu, "pos", menu);
    animation->setDuration(std::max(1, durationMs));
    animation->setStartValue(menu->pos());
    animation->setEndValue(target);
    animation->setEasingCurve(QEasingCurve::OutCubic);
    animation->start();
    return animation;
}

} // namespace Pet::UI
