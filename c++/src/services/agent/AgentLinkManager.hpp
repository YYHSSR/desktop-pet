#pragma once

#include "ChatGptMonitor.hpp"
#include "IAgentLinkHost.hpp"
#include "IAgentMonitor.hpp"

#include <QHash>
#include <QObject>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QVariantMap>

#include <memory>

class QTimer;

namespace Pet::Infrastructure {
class ConfigManager;
}

namespace Pet::Services {

// 1:1 移植自 Python pet/services/agent_link.py 的 AgentLinkManager。
//
// 多 Agent 联动总调度管理器：持有已配置的 Agent 监视器，并根据状态驱动
// 桌宠动作与气泡。状态词汇统一为 idle / thinking / working / attention /
// sleeping / error，映射到写代码 / 敲击 / 气泡提示 / 报错提示 / 待机。
class AgentLinkManager : public QObject {
    Q_OBJECT
public:
    AgentLinkManager(IAgentLinkHost* host, Infrastructure::ConfigManager* config,
                     QObject* parent = nullptr, double minInterval = 2.0);
    ~AgentLinkManager() override;

    // Python AGENT_NAMES：内置展示名
    static const QHash<QString, QString>& builtinAgentNames();
    // Python TOOL_LABELS：工具名 → 用户可读过程文案
    static const QHash<QString, QString>& toolLabels();
    static QString unknownToolLabel();

    // 根据配置启停各个 Agent 监视器。
    void applyConfig();
    void stop();

    // 开启或关闭指定 Agent 监视器。
    // 返回 false 表示未生效（hooks 安装失败 / 未知 key），调用方应回滚 UI 勾选态。
    bool setEnabled(const QString& agentKey, bool enabled);

    // 桌宠隐藏时暂停所有监视器；恢复显示时恢复活动的监视器。
    void pause();
    void resume();

    [[nodiscard]] QStringList agentKeys() const;
    [[nodiscard]] QString agentDisplayName(const QString& agentKey) const;
    // 前台窗口是否属于「联动开启且正在忙」的 Agent（进程名或窗口标题命中）。
    [[nodiscard]] bool busyAgentOwnsProcess(const QString& processName,
                                            const QString& title = {}) const;

private slots:
    void onAgentState(const QString& agentKey, const QString& state);
    void onAgentActivity(const QString& agentKey, const QString& tool);
    void resetChatGptTask();
    void onChatGptSnapshot(const CodexSnapshot& snapshot);
    void fireDone(const QString& agentKey);

private:
    [[nodiscard]] QString configDir() const;
    QVariantMap agentConfig() const;
    QString nextLinkAnimRotation();
    QString nextBusyAnim();
    void animateAgentTool(const QString& agentKey, const QString& tool);
    void scheduleDoneCheck(const QString& agentKey);
    void cancelDoneCheck(const QString& agentKey);
    void showLinkBubble(const QString& text, bool important, int durationMs = 4500,
                        int retried = 0);
    void warnIfAgentAbsent(const QString& agentKey);
    [[nodiscard]] QString thinkingText(const QString& agentKey) const;
    void maybeNotifyStart(const QString& agentKey, const QString& previousRaw,
                          const QString& state);
    [[nodiscard]] QStringList actNames() const;
    [[nodiscard]] bool anyBusy() const;

    IAgentLinkHost* m_host = nullptr;
    Infrastructure::ConfigManager* m_config = nullptr;

    // 状态节流：同一 Agent 两次动作切换最小间隔（毫秒）
    qint64 m_minIntervalMs = 2000;

    QHash<QString, QPair<QString, qint64>> m_lastApplied; // agent -> (state, ms)
    // 原始状态流（不受去抖/节流影响）：用于 busy→idle 完成检测
    QHash<QString, QString> m_lastRaw;
    QHash<QString, QTimer*> m_doneTimers; // 每个 Agent 复用一个，避免回调期间销毁 QObject
    QSet<QString> m_donePending;
    QHash<QString, qint64> m_doneCooldown;
    QSet<QString> m_sawAlert; // busy 周期内出现过 attention/error
    QSet<QString> m_sawError; // busy 周期内真正出现过 error

    int m_linkSeq = 0; // 联动动作轮换计数
    QHash<QString, QPair<QString, qint64>> m_lastToolAnim;

    QHash<QString, QPair<QString, qint64>> m_lastActivity;
    qint64 m_activityGlobalLast = 0;
    qint64 m_linkBubbleBusyUntil = 0;

    CodexSnapshot m_chatgptSnapshot;

    // 监视器由 QObject 父子关系持有（构造时 parent 均为 this）
    QHash<QString, IAgentMonitor*> m_monitors;
    QHash<QString, QString> m_agentNames;
};

} // namespace Pet::Services
