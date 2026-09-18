#pragma once

#include <QIcon>
#include <QImage>
#include <QPixmap>
#include <QString>
#include <QWidget>

namespace Pet::UI {

// 对应 Python pet/ui/context_menus/icons.py 与 quick_launch.py 的图标工具。
// 全部图标在 16 单位坐标系内绘制（Python: painter.scale(size / 16.0, ...)）。
class MenuIcons {
public:
    // Python small_icon_size(widget)
    static int smallIconSize(const QWidget* widget);

    // Python vector_menu_icon(menu, name, size=None)
    static QIcon vectorMenuIcon(const QWidget* widget, const QString& name, int size = -1);

    // Python fitted_pet_pixmap_icon(menu, source)
    static QIcon fittedPetPixmapIcon(const QWidget* widget, const QPixmap& source);

    // Python quick_launch.fitted_application_icon(icon, size, widget)
    static QIcon fittedApplicationIcon(const QIcon& icon, int size, const QWidget* widget);

    // Python quick_launch.quick_app_icon(menu, item)：含 (kind, path) 静态缓存。
    static QIcon quickAppIcon(const QWidget* widget, const QString& kind, const QString& path);
};

} // namespace Pet::UI
