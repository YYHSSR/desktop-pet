// 动作仲裁器测试：拖拽中拒绝、TTL 过期、generation 不匹配、action_id 解析、
// 气泡位让路与有限重试、断线清除待处理意图。
// IAgentLinkHost 用打桩实现，只记录调用，不真的渲染。
#include "services/behavior/ActionArbiter.hpp"

#include <QCoreApplication>
#include <QEventLoop>
#include <QTimer>

#include <cassert>
#include <iostream>

namespace {

using namespace Pet::Services;

class StubHost final : public IAgentLinkHost {
public:
    QStringList playedAnims;
    QStringList bubbles;
    bool idleRequested = false;
    int clearPendingCalls = 0;
    qint64 busyUntil = 0;
    bool visible = true;

    QStringList animationNamesFor(const QString& folder) const override
    {
        Q_UNUSED(folder);
        return {};
    }
    void requestLinkAnim(const QString& animationName) override { playedAnims.append(animationName); }
    void requestLinkIdle() override { idleRequested = true; }
    void clearPendingLinkAnim() override { ++clearPendingCalls; }
    void setLinkNextAnimProvider(std::function<QString()> provider) override { Q_UNUSED(provider); }
    void showBubble(const QString& text, int durationMs) override { bubbles.append(text + u'|' + QString::number(durationMs)); }
    qint64 bubbleBusyUntilMs() const override { return busyUntil; }
    bool petVisible() const override { return visible; }
};

struct Receipt {
    QString requestId;
    QString status;
    QString reason;
};

int counter = 0;

BehaviorProposal makeProposal(const QString& actionId, const QString& bubbleText = {})
{
    BehaviorProposal proposal;
    proposal.requestId = QStringLiteral("prop-%1").arg(++counter);
    proposal.actionId = actionId;
    proposal.bubbleText = bubbleText;
    proposal.bubbleImportant = !bubbleText.isEmpty();
    proposal.generation = 7;
    proposal.configRevision = 12;
    proposal.ttlMs = 2000;
    return proposal;
}

void waitMs(int milliseconds)
{
    QEventLoop loop;
    QTimer::singleShot(milliseconds, &loop, &QEventLoop::quit);
    loop.exec();
}

void runAll()
{
    StubHost host;
    ActionArbiter arbiter(&host);

    qint64 now = 0;
    arbiter.setMonotonicClock([&now] { return now; });

    QHash<QString, QString> catalog;
    catalog.insert(QStringLiteral("random/写代码"), QStringLiteral("写代码"));
    catalog.insert(QStringLiteral("click/招手"), QStringLiteral("招手"));
    arbiter.setActionCatalog(catalog);
    arbiter.setGeneration(7);
    arbiter.setConfigRevision(12);

    QList<Receipt> receipts;
    QObject::connect(&arbiter, &ActionArbiter::resultReady,
                     [&receipts](const QString& requestId, const QString& status, const QString& reason) {
                         receipts.append({requestId, status, reason});
                     });

    // 1) 正常路径：动作解析到本地动画名，气泡与动作都执行，accepted 不带 reason
    arbiter.submit(makeProposal(QStringLiteral("random/写代码"), QStringLiteral("干完活啦～")));
    assert(receipts.last().status == QStringLiteral("accepted"));
    assert(receipts.last().reason.isEmpty());
    assert(host.playedAnims == QStringList{QStringLiteral("写代码")});
    assert(host.bubbles.size() == 1);
    assert(host.bubbles.first().startsWith(QStringLiteral("干完活啦～|")));

    // 2) 拖拽/下落锁：现场动作绝不被打断
    arbiter.setInteractionLocked(true);
    arbiter.submit(makeProposal(QStringLiteral("click/招手")));
    assert(receipts.last().status == QStringLiteral("rejected"));
    assert(receipts.last().reason == QStringLiteral("interaction_locked"));
    assert(host.playedAnims.size() == 1); // 没有新动作
    arbiter.setInteractionLocked(false);

    // 3) generation 不匹配 → expired（旧代次的建议已失去意义）
    BehaviorProposal stale = makeProposal(QStringLiteral("click/招手"));
    stale.generation = 6;
    arbiter.submit(stale);
    assert(receipts.last().status == QStringLiteral("expired"));
    assert(receipts.last().reason == QStringLiteral("generation_mismatch"));
    assert(host.playedAnims.size() == 1);

    // 4) config revision 不匹配
    BehaviorProposal oldRevision = makeProposal(QStringLiteral("click/招手"));
    oldRevision.configRevision = 11;
    arbiter.submit(oldRevision);
    assert(receipts.last().status == QStringLiteral("rejected"));
    assert(receipts.last().reason == QStringLiteral("revision_mismatch"));

    // 5) action_id 解析不到本地动画 → unknown_action，什么都不播
    arbiter.submit(makeProposal(QStringLiteral("random/不存在")));
    assert(receipts.last().status == QStringLiteral("rejected"));
    assert(receipts.last().reason == QStringLiteral("unknown_action"));
    assert(host.playedAnims.size() == 1);

    // 6) 通道不健康 → channel_unhealthy
    arbiter.setChannelHealthy(false);
    arbiter.submit(makeProposal(QStringLiteral("click/招手")));
    assert(receipts.last().reason == QStringLiteral("channel_unhealthy"));
    arbiter.setChannelHealthy(true);

    // 7) system/idle：不是角色包动作，由仲裁器直接解释为回到待机
    arbiter.submit(makeProposal(QStringLiteral("system/idle")));
    assert(receipts.last().status == QStringLiteral("accepted"));
    assert(host.idleRequested);

    // 8) 气泡位被占用：普通气泡直接让位被拒，不排队
    host.busyUntil = 5000;
    BehaviorProposal casual = makeProposal(QStringLiteral("system/bubble"), QStringLiteral("随便说说"));
    casual.bubbleImportant = false;
    arbiter.submit(casual);
    assert(receipts.last().status == QStringLiteral("rejected"));
    assert(receipts.last().reason == QStringLiteral("bubble_busy"));
    assert(host.bubbles.size() == 1);

    // 9) 重要提示让路重试：气泡位空出来后补播，accepted
    arbiter.submit(makeProposal(QStringLiteral("system/bubble"), QStringLiteral("干完活啦，去看看成果吧～")));
    assert(receipts.size() == 8); // 还没有回执：正在等气泡位
    host.busyUntil = 0;           // 让路成功
    waitMs(700);                  // 越过重试间隔（400ms）
    assert(receipts.last().status == QStringLiteral("accepted"));
    assert(host.bubbles.size() == 2);

    // 10) 让路重试同样受 TTL 约束：气泡位一直不空、超过 TTL 后作废，不发言
    now = 10000;
    host.busyUntil = 20000; // 永远被占用
    BehaviorProposal doomed = makeProposal(QStringLiteral("system/bubble"), QStringLiteral("重要但等不到了"));
    doomed.ttlMs = 500; // 人为缩短：第一次重试前就会越过
    arbiter.submit(doomed);
    assert(receipts.size() == 9);
    waitMs(700);
    assert(receipts.last().status == QStringLiteral("expired"));
    assert(host.bubbles.size() == 2);

    // 11) clearPending：断线/隐藏时清掉待处理意图，仍给 Worker 一个 expired 回执
    host.busyUntil = 30000;
    arbiter.submit(makeProposal(QStringLiteral("system/bubble"), QStringLiteral("等位中")));
    assert(receipts.size() == 10);
    arbiter.clearPending();
    assert(receipts.last().status == QStringLiteral("expired"));
    assert(host.bubbles.size() == 2);
    host.busyUntil = 0;
    waitMs(700);
    assert(host.bubbles.size() == 2); // 清除后绝不补播

    // 12) generation 变化自动作废待重试建议
    host.busyUntil = 30000;
    arbiter.submit(makeProposal(QStringLiteral("system/bubble"), QStringLiteral("代次要变了")));
    arbiter.setGeneration(8);
    assert(receipts.last().status == QStringLiteral("expired"));
    host.busyUntil = 0;
    waitMs(700);
    assert(host.bubbles.size() == 2);

    std::cout << "action arbiter: all ok" << std::endl;
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    runAll();
    return 0;
}
