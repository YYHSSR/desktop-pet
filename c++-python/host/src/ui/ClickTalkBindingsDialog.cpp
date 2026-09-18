#include "ClickTalkBindingsDialog.hpp"

#include "infrastructure/ConfigManager.hpp"

#include <QHBoxLayout>
#include <QLabel>
#include <QListWidget>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QVBoxLayout>

namespace Pet::UI {

ClickTalkBindingsDialog::ClickTalkBindingsDialog(Infrastructure::ConfigManager* config,
                                                 QStringList clickNames, QWidget* parent)
    : QDialog(parent)
    , m_config(config)
    , m_clickNames(std::move(clickNames)) {
    if (m_config != nullptr) {
        m_characterId = m_config->character();
        m_bindings = m_config->clickTalkBindings(m_characterId);
    }

    setWindowTitle(QString::fromUtf8(u8"点击动画台词绑定"));
    resize(560, 420);
    setMinimumSize(480, 360);

    auto* layout = new QVBoxLayout(this);

    auto* hint = new QLabel(
        QString::fromUtf8(u8"每个点击动画可绑定多条专属自言自语台词，每行一条。\n"
                          u8"点击桌宠时会优先播放当前动画绑定的台词；未绑定时回退全局随机自言自语。"),
        this);
    hint->setWordWrap(true);
    layout->addWidget(hint);

    auto* body = new QHBoxLayout();

    m_list = new QListWidget(this);
    m_list->addItems(m_clickNames);
    m_list->setFixedWidth(200);
    body->addWidget(m_list);

    auto* right = new QVBoxLayout();
    right->addWidget(new QLabel(QString::fromUtf8(u8"绑定台词（每行一条）："), this));
    m_textEdit = new QPlainTextEdit(this);
    right->addWidget(m_textEdit);
    body->addLayout(right);

    layout->addLayout(body);

    auto* buttons = new QHBoxLayout();
    buttons->addStretch(1);
    auto* cancel = new QPushButton(QString::fromUtf8(u8"取消"), this);
    auto* saveButton = new QPushButton(QString::fromUtf8(u8"保存"), this);
    connect(saveButton, &QPushButton::clicked, this, &ClickTalkBindingsDialog::save);
    connect(cancel, &QPushButton::clicked, this, &QDialog::reject);
    buttons->addWidget(cancel);
    buttons->addWidget(saveButton);
    layout->addLayout(buttons);

    connect(m_list, &QListWidget::currentRowChanged, this, &ClickTalkBindingsDialog::loadSelected);
    if (!m_clickNames.isEmpty()) {
        m_list->setCurrentRow(0);
    }
}

void ClickTalkBindingsDialog::loadSelected(int row) {
    if (row < 0 || row >= m_clickNames.size()) {
        m_textEdit->setPlainText(QString());
        return;
    }
    const QString actionId = m_clickNames.at(row);
    m_textEdit->setPlainText(m_bindings.value(actionId).toStringList().join(QLatin1Char('\n')));
}

void ClickTalkBindingsDialog::save() {
    const int row = m_list->currentRow();
    if (row >= 0 && row < m_clickNames.size()) {
        const QString actionId = m_clickNames.at(row);
        QStringList texts;
        const QStringList lines = m_textEdit->toPlainText().split(QLatin1Char('\n'));
        for (const QString& line : lines) {
            const QString stripped = line.trimmed();
            if (!stripped.isEmpty()) {
                texts.append(stripped);
            }
        }
        if (!texts.isEmpty()) {
            m_bindings.insert(actionId, texts.mid(0, 50));
        } else {
            m_bindings.remove(actionId);
        }
    }
    if (m_config != nullptr) {
        m_config->setClickTalkBindings(m_characterId, m_bindings);
    }
    accept();
}

} // namespace Pet::UI
