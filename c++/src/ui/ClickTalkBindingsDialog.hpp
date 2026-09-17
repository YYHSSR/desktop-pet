#pragma once

#include <QDialog>
#include <QStringList>
#include <QVariantMap>

class QListWidget;
class QPlainTextEdit;

namespace Pet::Infrastructure {
class ConfigManager;
}

namespace Pet::UI {

// 1:1 移植自 Python pet/ui/click_talk_dialog.py。
//
// 每个点击动画可绑定多条专属自言自语台词；点击角色时优先播放当前动画绑定的
// 台词，未绑定时回退全局随机自言自语。
class ClickTalkBindingsDialog : public QDialog {
    Q_OBJECT
public:
    ClickTalkBindingsDialog(Infrastructure::ConfigManager* config, QStringList clickNames,
                            QWidget* parent = nullptr);

private slots:
    void loadSelected(int row);
    void save();

private:
    Infrastructure::ConfigManager* m_config = nullptr;
    QString m_characterId;
    QStringList m_clickNames;
    QVariantMap m_bindings;
    QListWidget* m_list = nullptr;
    QPlainTextEdit* m_textEdit = nullptr;
};

} // namespace Pet::UI
