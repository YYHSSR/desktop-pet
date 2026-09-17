#include "ModernContextMenu.hpp"

#include "PetWindowController.hpp"
#include "context_menus/MenuExtras.hpp"
#include "context_menus/MenuPositioning.hpp"
#include "context_menus/MenuPrimitives.hpp"
#include "context_menus/MenuStyle.hpp"
#include "infrastructure/ConfigManager.hpp"
#include "media/CharacterCatalog.hpp"

#include <QApplication>
#include <QDesktopServices>
#include <QScreen>
#include <QThreadPool>
#include <QTimer>
#include <QUrl>
#include <algorithm>
#include <functional>
#include <vector>

namespace Pet::UI {

ModernContextMenu::ModernContextMenu(PetWindowController* controller,
                                     Infrastructure::ConfigManager* config,
                                     QWidget* parent)
    : QMenu(parent)
    , m_controller(controller)
    , m_config(config) {
    setAttribute(Qt::WA_TranslucentBackground, true);
    setWindowFlags(windowFlags() | Qt::FramelessWindowHint | Qt::NoDropShadowWindowHint);

    // Python populate_context_menu：先落样式再装配，子菜单通过 inherit_menu_style 继承。
    const MenuAppearance appearance = menuAppearanceFromMap(
        m_config != nullptr ? m_config->menuAppearanceValues() : QVariantMap());
    applyModernMenuStyle(this, appearance);

    buildMenu();
}

void ModernContextMenu::buildMenu() {
    // 顺序严格对齐 Python build_modern_menu：
    // 彩蛋首行 → 播放 → 功能 → 工具（含 Agent 联动）→ 设置 → 退出。
    const QVariantMap easterEgg = m_config != nullptr
        ? m_config->menuEasterEggValues()
        : QVariantMap();
    if (easterEgg.value(QStringLiteral("enabled"), true).toBool()) {
        addEasterEggEntry();
        addSeparator();
    }

    addPlaybackGroup();
    addFeaturesGroup();
    addToolsGroup();
    addSettingsAndQuitGroup();
}

void ModernContextMenu::startGroup() {
    const QList<QAction*> actions = this->actions();
    if (!actions.isEmpty() && !actions.last()->isSeparator()) {
        addSeparator();
    }
}

void ModernContextMenu::addEasterEggEntry() {
    const QVariantMap egg = m_config != nullptr
        ? m_config->menuEasterEggValues()
        : QVariantMap();
    addOjingjingEntry(this, egg);
}

void ModernContextMenu::addPlaybackGroup() {
    const MenuContext context{m_controller, m_config};
    startGroup();

    QMenu* animations = addMenuSubmenu(this, QString::fromUtf8(u8"播放动画"), QStringLiteral("play"));

    if (m_controller != nullptr) {
        const QString character = m_controller->currentCharacter();
        const QList<Media::AnimationCategory> categories =
            m_controller->catalog().animationCategoriesOrdered(character);

        QList<AnimationCategorySpec> specs;
        specs.reserve(categories.size());
        for (const Media::AnimationCategory& category : categories) {
            AnimationCategorySpec spec;
            spec.label = category.label;
            spec.files = category.files;
            if (category.label == QString::fromUtf8(u8"移动")) {
                spec.callback = [this](const QString& name) {
                    if (m_controller != nullptr) {
                        m_controller->triggerMoveAnimation(name);
                    }
                };
            } else {
                spec.callback = [this](const QString& name) {
                    if (m_controller != nullptr) {
                        m_controller->switchAnimation(name);
                    }
                };
            }
            specs.append(spec);
        }
        buildAnimationCategories(animations, specs, character, false, true);
    }

    buildCharacterMenu(this, context);
}

void ModernContextMenu::addFeaturesGroup() {
    const MenuContext context{m_controller, m_config};
    startGroup();

    buildSpeedMenu(this, context);
    buildSizeMenu(this, context);
    addReturnCorner(this, context);
    addHidePet(this, context);
    addNoMove(this, context);
    addOnTop(this, context);
    addAutostart(this, context);
}

void ModernContextMenu::addToolsGroup() {
    const MenuContext context{m_controller, m_config};
    startGroup();

    // 启动应用、快捷网址与 Agent 联动同属一个分组（无组间分隔线）。
    addAppLaunchMenu(this, context);
    addQuickWebsitesMenu(this, context);
    addAgentLinkMenu(this, context);
}

void ModernContextMenu::addSettingsAndQuitGroup() {
    const MenuContext context{m_controller, m_config};
    startGroup();

    addMenuAction(this, QString::fromUtf8(u8"桌宠设置"), QStringLiteral("settings"), [this]() {
        if (m_controller != nullptr) {
            m_controller->openModernSettings();
        }
    }, true);

    // Python shared.py add_update_help：设置与退出之间的「更新与帮助」链接子菜单
    QMenu* updateHelp = addMenuSubmenu(this, QString::fromUtf8(u8"更新与帮助"),
                                       QStringLiteral("update"));
    addMenuAction(updateHelp, QString::fromUtf8(u8"GitHub 项目页"), QStringLiteral("web"), []() {
        QDesktopServices::openUrl(QUrl(QStringLiteral("https://github.com/MerZlin/dsh-pet-indesktop")));
    }, true);
#ifdef Q_OS_WIN
    addMenuAction(updateHelp, QString::fromUtf8(u8"夸克网盘下载"), QStringLiteral("download"), []() {
        QDesktopServices::openUrl(QUrl(QStringLiteral("https://pan.quark.cn/s/68fc681ae486")));
    }, true);
#endif

    startGroup();
    addQuit(this, context);
}

void ModernContextMenu::showContextMenu(PetWindowController* controller,
                                        Infrastructure::ConfigManager* config,
                                        const QPoint& globalPos) {
    Q_UNUSED(globalPos);

    auto* menu = new ModernContextMenu(controller, config, nullptr);
    menu->setAttribute(Qt::WA_DeleteOnClose, true);

    installStayOpenFilter(menu);
    installResponsiveMenuStyle(menu);
    installModernCheckIndicators(menu);

    if (controller != nullptr) {
        // 气泡是置顶窗口，层级高于原生菜单，右键时先隐藏。
        QObject::connect(menu, &QMenu::aboutToHide, menu, [controller]() {
            QTimer::singleShot(0, controller, [controller]() {
                controller->restoreOnTopAfterContextMenu();
            });
        });
    }

    // 根菜单避让角色且始终保持 LTR 视觉方向。
    const QRect petRect = controller != nullptr ? controller->visibleContentRect() : QRect();
    QScreen* screen = controller != nullptr ? controller->currentScreen()
                                           : QApplication::primaryScreen();
    const QRect available = screen != nullptr ? screen->availableGeometry() : QRect();

    int submenuWidth = 0;
    const QList<QMenu*> submenus = menu->findChildren<QMenu*>();
    for (QMenu* child : submenus) {
        submenuWidth = std::max(submenuWidth, child->sizeHint().width());
    }

    const QPoint popupPos = MenuPositioning::pickContextMenuPosition(
        petRect, menu->sizeHint(), submenuWidth, available);

    menu->setLayoutDirection(Qt::LeftToRight);
    for (QMenu* child : submenus) {
        child->setLayoutDirection(Qt::LeftToRight);
    }

    const QSize menuSize = menu->sizeHint();
    const int slideTowardPet = popupPos.x() < petRect.center().x() ? 18 : -18;
    const QPoint transitionStart = MenuPositioning::clampMenuRect(
        QRect(popupPos.x() + slideTowardPet, popupPos.y(),
              menuSize.width(), menuSize.height()),
        available).topLeft();

    QObject::connect(menu, &QMenu::aboutToShow, menu, [menu, popupPos]() {
        QTimer::singleShot(0, menu, [menu, popupPos]() {
            MenuPositioning::animateContextMenuTo(menu, popupPos);
        });
    });

    menu->exec(transitionStart);

    // 必须在原生菜单跟踪循环结束后执行需要新建顶层窗口的命令。
    std::vector<std::function<void()>> callbacks;
    takeDeferredMenuCallbacks(menu, callbacks);
    if (!callbacks.empty()) {
        QObject::connect(menu, &QObject::destroyed, qApp, [callbacks]() {
            QTimer::singleShot(0, qApp, [callbacks]() {
                for (const std::function<void()>& callback : callbacks) {
                    callback();
                }
            });
        });
    }

    // 先清掉尚未启动的解码任务，避免线程池析构时长时间等待。
    for (QMenu* child : menu->findChildren<QMenu*>()) {
        if (QThreadPool* pool = child->findChild<QThreadPool*>()) {
            pool->clear();
        }
    }
    menu->deleteLater();
}

} // namespace Pet::UI
