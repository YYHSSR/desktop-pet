#pragma once

#include "services/agent/IAgentLinkHost.hpp"

#include <QElapsedTimer>
#include <QHash>
#include <QObject>
#include <QTimer>
#include <QString>

#include <functional>

namespace Pet::Services {

// Worker 提交的一条行为建议（behavior.propose 的 C++ 侧形态）
struct BehaviorProposal {
    QString requestId;        // 用于去重与结果关联
    QString actionId;         // 稳定语义标识；"system/idle" 表示回到待机
    QString bubbleText;       // 可空
    bool bubbleImportant = false;
    int bubbleDurationMs = 4500;
    quint64 generation = 0;   // 生成该建议时的宿主代次
    int configRevision = 0;
    int ttlMs = 2000;         // 默认 2 秒
};

// 动作仲裁器：Worker 只提建议，「此刻是否允许执行」由宿主决定。
//
// 规则（依序判定，先到先拒）：
// 1. 通道不健康 → rejected/channel_unhealthy（重启期间不再接新建议）；
// 2. 拖拽/下落锁 → rejected/interaction_locked（现场动作绝不被打断）；
// 3. generation 不匹配 → expired/generation_mismatch（旧代次的建议已失去意义）；
// 4. config revision 不匹配 → rejected/revision_mismatch；
// 5. TTL 超期 → expired（超期建议作废，不发动作、不发言）；
// 6. action_id 解析不到本地动画 → rejected/unknown_action；
// 7. 气泡位被占用：普通气泡直接 rejected/bubble_busy；重要气泡让路重试
//    （有限次，重试期间同样受 TTL 约束）。
class ActionArbiter : public QObject {
    Q_OBJECT
public:
    explicit ActionArbiter(IAgentLinkHost* host, QObject* parent = nullptr);

    // action_id → 动画名的解析表；由宿主在角色/目录变化时整表替换
    void setActionCatalog(const QHash<QString, QString>& actionIdToName);
    void setGeneration(quint64 generation);
    void setInteractionLocked(bool locked);
    void setConfigRevision(int revision);
    void setChannelHealthy(bool healthy);
    // 断线或隐藏时清除待处理意图：不再执行，但给 Worker 一个 expired 回执
    void clearPending();

    void submit(const BehaviorProposal& proposal);

    // 单调时钟可注入（测试用）；缺省为进程启动起算的单调毫秒
    void setMonotonicClock(std::function<qint64()> monotonicMs);

signals:
    void resultReady(const QString& requestId, const QString& status, const QString& reason);

private:
    void finish(const QString& requestId, const QString& status, const QString& reason);
    void execute(const BehaviorProposal& proposal);
    void retryBubble();
    [[nodiscard]] bool bubbleBusy() const;

    IAgentLinkHost* m_host;
    QHash<QString, QString> m_actionCatalog;
    quint64 m_generation = 0;
    bool m_interactionLocked = false;
    int m_configRevision = -1; // -1：尚未同步过配置，不校验
    bool m_channelHealthy = true;

    BehaviorProposal m_pendingBubble; // 等气泡位的待重试建议（同一时刻至多一条）
    bool m_hasPendingBubble = false;
    int m_bubbleRetries = 0;
    qint64 m_pendingSubmitMs = 0;
    QTimer m_retryTimer;
    QElapsedTimer m_monotonicBase;
    std::function<qint64()> m_monotonicMs;
};

} // namespace Pet::Services
