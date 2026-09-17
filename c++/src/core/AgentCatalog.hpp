#pragma once

#include <QString>
#include <QStringList>
#include <QVector>

namespace Pet::Core {

// 对应 Python pet/core/agents.py：菜单与 Agent 服务共用的唯一有序目录。
struct AgentMenuItem {
    QString key;
    QString label;
};

inline const QVector<AgentMenuItem>& agentMenuItems() {
    static const QVector<AgentMenuItem> items = {
        {QStringLiteral("antigravity"), QStringLiteral("Antigravity IDE")},
        {QStringLiteral("chatgpt"), QStringLiteral("ChatGPT")},
    };
    return items;
}

inline QString agentDisplayName(const QString& key) {
    for (const AgentMenuItem& item : agentMenuItems()) {
        if (item.key == key) {
            return item.label;
        }
    }
    return key;
}

} // namespace Pet::Core
