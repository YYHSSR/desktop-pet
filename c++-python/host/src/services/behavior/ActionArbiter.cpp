#include "services/behavior/ActionArbiter.hpp"

#include "services/bridge/ProtocolMessage.hpp"

namespace Pet::Services {

namespace {
constexpr int kMaxBubbleRetries = 3;
constexpr int kBubbleRetryIntervalMs = 400;
} // namespace

ActionArbiter::ActionArbiter(IAgentLinkHost* host, QObject* parent)
    : QObject(parent), m_host(host)
{
    m_monotonicBase.start();
    m_monotonicMs = [this] { return m_monotonicBase.elapsed(); };

    m_retryTimer.setSingleShot(true);
    connect(&m_retryTimer, &QTimer::timeout, this, &ActionArbiter::retryBubble);
}

void ActionArbiter::setMonotonicClock(std::function<qint64()> monotonicMs)
{
    if (monotonicMs) {
        m_monotonicMs = std::move(monotonicMs);
    }
}

void ActionArbiter::setActionCatalog(const QHash<QString, QString>& actionIdToName)
{
    m_actionCatalog = actionIdToName;
}

void ActionArbiter::setGeneration(quint64 generation)
{
    if (generation != m_generation) {
        // 代次变化让所有已发出的建议失去意义：作废待重试的气泡建议
        clearPending();
    }
    m_generation = generation;
}

void ActionArbiter::setInteractionLocked(bool locked)
{
    m_interactionLocked = locked;
}

void ActionArbiter::setConfigRevision(int revision)
{
    m_configRevision = revision;
}

void ActionArbiter::setChannelHealthy(bool healthy)
{
    m_channelHealthy = healthy;
    if (!healthy) {
        clearPending();
    }
}

void ActionArbiter::clearPending()
{
    m_retryTimer.stop();
    if (m_hasPendingBubble) {
        // 给 Worker 一个明确回执，别让它无限等 behavior.result
        finish(m_pendingBubble.requestId, QStringLiteral("expired"), QString());
        m_hasPendingBubble = false;
    }
    m_bubbleRetries = 0;
}

bool ActionArbiter::bubbleBusy() const
{
    return m_host != nullptr && m_host->bubbleBusyUntilMs() > m_monotonicMs();
}

void ActionArbiter::submit(const BehaviorProposal& proposal)
{
    const qint64 now = m_monotonicMs();

    if (!m_channelHealthy) {
        finish(proposal.requestId, QStringLiteral("rejected"), QStringLiteral("channel_unhealthy"));
        return;
    }
    if (m_interactionLocked) {
        // 拖拽/下落期间不被打断。要通知不受影响的话，Worker 应改发 system/bubble。
        finish(proposal.requestId, QStringLiteral("rejected"), QStringLiteral("interaction_locked"));
        return;
    }
    if (proposal.generation != m_generation) {
        finish(proposal.requestId, QStringLiteral("expired"), QStringLiteral("generation_mismatch"));
        return;
    }
    if (m_configRevision >= 0 && proposal.configRevision != m_configRevision) {
        finish(proposal.requestId, QStringLiteral("rejected"), QStringLiteral("revision_mismatch"));
        return;
    }
    if (m_host == nullptr) {
        finish(proposal.requestId, QStringLiteral("rejected"), QStringLiteral("channel_unhealthy"));
        return;
    }

    if (proposal.actionId != kSystemActionIdle && proposal.actionId != kSystemActionBubble
        && !m_actionCatalog.contains(proposal.actionId)) {
        // 白名单解析失败：Worker 只能在 pet.snapshot 下发的动作目录里挑
        finish(proposal.requestId, QStringLiteral("rejected"), QStringLiteral("unknown_action"));
        return;
    }

    if (!proposal.bubbleText.isEmpty() && bubbleBusy()) {
        if (!proposal.bubbleImportant || m_hasPendingBubble) {
            finish(proposal.requestId, QStringLiteral("rejected"), QStringLiteral("bubble_busy"));
            return;
        }
        // 重要提示让路：等气泡位空出来再重试（有限次，且同样受 TTL 约束）
        m_pendingBubble = proposal;
        m_pendingSubmitMs = now;
        m_hasPendingBubble = true;
        m_bubbleRetries = 0;
        m_retryTimer.start(kBubbleRetryIntervalMs);
        return;
    }

    execute(proposal);
}

void ActionArbiter::execute(const BehaviorProposal& proposal)
{
    // 气泡与动作独立执行：动作可以因锁被拒，通知不该跟着一起丢
    if (!proposal.bubbleText.isEmpty()) {
        m_host->showBubble(proposal.bubbleText, proposal.bubbleDurationMs);
    }
    if (proposal.actionId == kSystemActionIdle) {
        m_host->requestLinkIdle();
    } else if (proposal.actionId != kSystemActionBubble) {
        m_host->requestLinkAnim(m_actionCatalog.value(proposal.actionId));
    }
    finish(proposal.requestId, QStringLiteral("accepted"), QString());
}

void ActionArbiter::retryBubble()
{
    if (!m_hasPendingBubble) {
        return;
    }
    const BehaviorProposal pending = m_pendingBubble;

    if (!m_channelHealthy || m_interactionLocked || pending.generation != m_generation) {
        m_hasPendingBubble = false;
        finish(pending.requestId, QStringLiteral("expired"), QString());
        return;
    }
    if (m_monotonicMs() - m_pendingSubmitMs > pending.ttlMs) {
        // 让路重试也受 TTL 约束：超期直接作废，不发动作、不发言
        m_hasPendingBubble = false;
        finish(pending.requestId, QStringLiteral("expired"), QString());
        return;
    }
    if (bubbleBusy()) {
        if (++m_bubbleRetries >= kMaxBubbleRetries) {
            m_hasPendingBubble = false;
            finish(pending.requestId, QStringLiteral("rejected"), QStringLiteral("bubble_busy"));
            return;
        }
        m_retryTimer.start(kBubbleRetryIntervalMs);
        return;
    }

    m_hasPendingBubble = false;
    execute(pending);
}

void ActionArbiter::finish(const QString& requestId, const QString& status, const QString& reason)
{
    emit resultReady(requestId, status, reason);
}

} // namespace Pet::Services
