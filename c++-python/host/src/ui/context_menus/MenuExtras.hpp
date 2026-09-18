#pragma once

#include "MenuPrimitives.hpp"

#include <QMenu>
#include <QVariantMap>
#include <QWidgetAction>

namespace Pet::UI {

// 对应 Python pet/ui/context_menus/fun_entry.py：现代菜单首行的彩蛋入口。
QWidgetAction* addOjingjingEntry(QMenu* menu, const QVariantMap& config);

// 对应 Python pet/ui/context_menus/quick_launch.py
QMenu* addAppLaunchMenu(QMenu* menu, const MenuContext& context);
QMenu* addQuickWebsitesMenu(QMenu* menu, const MenuContext& context);

} // namespace Pet::UI
