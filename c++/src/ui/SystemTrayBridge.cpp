#include "SystemTrayBridge.hpp"

#include "PetWindowController.hpp"
#include "infrastructure/ConfigManager.hpp"

#include <QAction>
#include <QIcon>
#include <QMenu>
#include <QSignalBlocker>
#include <QStringList>

namespace Pet::UI {

SystemTrayBridge::SystemTrayBridge(PetWindowController* controller,
                                   Infrastructure::ConfigManager* config, QObject* parent)
    : QObject(parent)
    , m_controller(controller)
    , m_config(config)
    , m_trayIcon(new QSystemTrayIcon(this))
    , m_menu(new QMenu(nullptr)) {

    // Python：气泡是置顶 Tool 窗口，托盘菜单弹出前先隐藏，避免盖住菜单
    connect(m_menu, &QMenu::aboutToShow, this, &SystemTrayBridge::beforeMenuShown);

    auto* actToggle = m_menu->addAction(QString::fromUtf8(u8"显示 / 隐藏"));
    connect(actToggle, &QAction::triggered, this, &SystemTrayBridge::toggleVisibility);

    auto* actSettings = m_menu->addAction(QString::fromUtf8(u8"桌宠设置"));
    connect(actSettings, &QAction::triggered, this, &SystemTrayBridge::openSettingsRequested);

    m_characterMenu = m_menu->addMenu(QString::fromUtf8(u8"切换角色"));
    if (m_controller != nullptr) {
        const QStringList characters = m_controller->availableCharacters();
        const QString current = m_controller->currentCharacter();
        for (const QString& characterId : characters) {
            QAction* action = m_characterMenu->addAction(characterId);
            action->setCheckable(true);
            action->setChecked(characterId == current);
            connect(action, &QAction::triggered, this, [this, characterId]() {
                if (m_controller != nullptr) {
                    m_controller->requestSwitchCharacter(characterId);
                }
            });
        }
    }

    m_menu->addSeparator();

    m_autostartAction = m_menu->addAction(QString::fromUtf8(u8"开机自启"));
    m_autostartAction->setCheckable(true);
    connect(m_autostartAction, &QAction::toggled, this, [this](bool enabled) {
        if (m_config == nullptr) {
            return;
        }
        m_config->setAutostartEnabled(enabled);
        m_config->setValue(QStringLiteral("autostart_wanted"), enabled);
    });

    m_menu->addSeparator();

    auto* actQuit = m_menu->addAction(QString::fromUtf8(u8"退出"));
    connect(actQuit, &QAction::triggered, this, &SystemTrayBridge::quitRequested);

    m_trayIcon->setContextMenu(m_menu);
    connect(m_trayIcon, &QSystemTrayIcon::activated, this, &SystemTrayBridge::onTrayActivated);

    syncCheckStates();
}

SystemTrayBridge::~SystemTrayBridge() {
    delete m_menu;
}

void SystemTrayBridge::initTray(const QString& iconPath) {
    if (!iconPath.isEmpty()) {
        m_trayIcon->setIcon(QIcon(iconPath));
    }
    m_trayIcon->setToolTip(QString::fromUtf8(u8"desktop-pet 桌宠"));
    m_trayIcon->show();
}

void SystemTrayBridge::toggleVisibility() {
    if (m_controller == nullptr) {
        return;
    }
    // Python toggle_visible：隐藏走 notify 路径，由控制器触发托盘提示
    m_controller->setPetVisible(!m_controller->petVisible(), /*notify=*/true);
}

void SystemTrayBridge::syncCheckStates() {
    // Python sync_tray_checks：设置对话框/右键菜单改过的开关，弹出前同步复选状态
    if (m_config == nullptr) {
        return;
    }
    if (m_autostartAction != nullptr) {
        const QSignalBlocker blocker(m_autostartAction);
        m_autostartAction->setChecked(m_config->autostartEnabled());
    }
    if (m_characterMenu != nullptr && m_controller != nullptr) {
        const QString current = m_controller->currentCharacter();
        const QList<QAction*> actions = m_characterMenu->actions();
        for (QAction* action : actions) {
            const QSignalBlocker blocker(action);
            action->setChecked(action->text() == current);
        }
    }
}

void SystemTrayBridge::beforeMenuShown() {
    // Python menu.aboutToShow：先隐藏置顶气泡，再同步复选状态
    if (m_controller != nullptr) {
        m_controller->hideBubble();
    }
    syncCheckStates();
}

void SystemTrayBridge::notifyPetHidden() {
    // Python _notify_pet_hidden
    m_trayIcon->showMessage(QString::fromUtf8(u8"桌宠已隐藏"),
                            QString::fromUtf8(u8"点击托盘图标或 Dock 图标即可恢复。"),
                            QSystemTrayIcon::Information, 4000);
}

void SystemTrayBridge::onTrayActivated(QSystemTrayIcon::ActivationReason reason) {
    if (reason == QSystemTrayIcon::DoubleClick) {
        toggleVisibility();
    }
}

} // namespace Pet::UI
