#pragma once

#include <QMenu>
#include <QPoint>
#include <QRect>

namespace Pet::Infrastructure {
class ConfigManager;
}

namespace Pet::UI {

class PetWindowController;

// 现代右键菜单：只负责按 Python build_modern_menu 的顺序装配与弹出流程，
// 图标、样式、原语、懒加载分别下沉到 context_menus/ 下的模块。
class ModernContextMenu : public QMenu {
    Q_OBJECT
public:
    explicit ModernContextMenu(PetWindowController* controller,
                               Infrastructure::ConfigManager* config,
                               QWidget* parent = nullptr);

    // 弹出菜单（阻塞式，与 Python menu.exec() 对齐），
    // 返回后执行延迟回调并释放整棵菜单树。
    static void showContextMenu(PetWindowController* controller,
                                Infrastructure::ConfigManager* config,
                                const QPoint& globalPos);

private:
    void buildMenu();
    // Python modern.py 的 start_group()：上一项不是分隔符时才补分隔线。
    void startGroup();
    void addEasterEggEntry();
    void addPlaybackGroup();
    void addFeaturesGroup();
    void addToolsGroup();
    void addSettingsAndQuitGroup();

    PetWindowController* m_controller = nullptr;
    Infrastructure::ConfigManager* m_config = nullptr;
};

} // namespace Pet::UI
