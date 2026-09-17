#pragma once

#include "infrastructure/ConfigManager.hpp"
#include "ui/PetWindowController.hpp"

#include <QAction>
#include <QImage>
#include <QList>
#include <QMenu>
#include <QObject>
#include <QPair>
#include <QRunnable>
#include <QString>
#include <QStringList>
#include <functional>
#include <vector>

namespace Pet::UI {

// 菜单构建过程中始终需要的一对依赖（Python 里是单个 pet 对象）。
struct MenuContext {
    PetWindowController* controller = nullptr;
    Infrastructure::ConfigManager* config = nullptr;
};

// ---------------------------------------------------------------- 通用原语（shared.py）

// Python defer_menu_callback / take_deferred_menu_callbacks
void takeDeferredMenuCallbacks(QMenu* menu, std::vector<std::function<void()>>& out);
bool deferMenuCallback(QMenu* menu, std::function<void()> callback);

// Python connect_action：closeOnTrigger 且父菜单可见时延迟到菜单关闭后执行。
void connectAction(QAction* action, std::function<void()> callback);

QAction* addMenuAction(QMenu* menu, const QString& text, const QString& iconName,
                       std::function<void()> callback = {}, bool closeOnTrigger = false);
QMenu* addMenuSubmenu(QMenu* menu, const QString& text, const QString& iconName = {});

// ---------------------------------------------------------------- 播放动画分类子菜单

// label + 文件列表 + 触发回调（Python 的 entries 与 callback）
struct AnimationCategorySpec {
    QString label;
    QStringList files;
    std::function<void(const QString& animationName)> callback;
};

// Python build_animation_categories：aboutToShow 懒填充 + 首帧缩略图。
// character 用于缩略图缓存分区（Python 侧缓存挂在各自窗口上）。
void buildAnimationCategories(QMenu* menu, const QList<AnimationCategorySpec>& categories,
                              const QString& character,
                              bool legacyLabels = false, bool leafRoleIcons = false);

// ---------------------------------------------------------------- 条目构建

QMenu* buildSpeedMenu(QMenu* menu, const MenuContext& context);
QMenu* buildSizeMenu(QMenu* menu, const MenuContext& context);
QMenu* buildCharacterMenu(QMenu* menu, const MenuContext& context);

QAction* addReturnCorner(QMenu* menu, const MenuContext& context);
QAction* addHidePet(QMenu* menu, const MenuContext& context);
QAction* addNoMove(QMenu* menu, const MenuContext& context);
QAction* addOnTop(QMenu* menu, const MenuContext& context);
QAction* addAutostart(QMenu* menu, const MenuContext& context);
QMenu* addAgentLinkMenu(QMenu* menu, const MenuContext& context);
QAction* addQuit(QMenu* menu, const MenuContext& context);

} // namespace Pet::UI
