#pragma once

#include <QObject>
#include <QSystemTrayIcon>

class QAction;
class QMenu;

namespace Pet::Infrastructure {
class ConfigManager;
}

namespace Pet::UI {

class PetWindowController;

// 系统托盘（对齐 Python pet/application/app.py 的 _build_tray）：
// 显示/隐藏、桌宠设置、切换角色、鼠标穿透、开机自启、退出；
// 菜单弹出前收起气泡并同步复选状态。
class SystemTrayBridge : public QObject {
    Q_OBJECT
public:
    SystemTrayBridge(PetWindowController* controller, Infrastructure::ConfigManager* config,
                     QObject* parent = nullptr);
    ~SystemTrayBridge() override;

    void initTray(const QString& iconPath);

    // Python _notify_pet_hidden：用户主动隐藏后提示恢复入口
    void notifyPetHidden();

signals:
    void openSettingsRequested();
    void quitRequested();

private slots:
    void onTrayActivated(QSystemTrayIcon::ActivationReason reason);
    void beforeMenuShown();

private:
    void toggleVisibility();
    void syncCheckStates();

    PetWindowController* m_controller = nullptr;
    Infrastructure::ConfigManager* m_config = nullptr;
    QSystemTrayIcon* m_trayIcon = nullptr;
    QMenu* m_menu = nullptr;
    QMenu* m_characterMenu = nullptr;
    QAction* m_autostartAction = nullptr;
};

} // namespace Pet::UI
