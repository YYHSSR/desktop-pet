#include "services/bridge/PythonServiceSupervisor.hpp"

#include <QCoreApplication>
#include <QJsonArray>

#include <algorithm>

namespace Pet::Services {

PythonServiceSupervisor::PythonServiceSupervisor(PythonBridge* bridge, QObject* parent)
    : QObject(parent), m_bridge(bridge)
{
    m_handshakeTimer.setSingleShot(true);
    m_graceTimer.setSingleShot(true);
    m_restartTimer.setSingleShot(true);

    connect(&m_handshakeTimer, &QTimer::timeout, this, [this] {
        // 5 秒内没完成握手：按故障处理（计入重启预算），不让宿主一直空等
        handleFailure(QStringLiteral("handshake_timeout"));
    });

    connect(&m_graceTimer, &QTimer::timeout, this, [this] {
        // 优雅关闭超时：Worker 没在预算内退出，直接杀
        m_bridge->abort();
    });

    connect(&m_restartTimer, &QTimer::timeout, this, [this] { beginHandshake(); });

    connect(m_bridge, &PythonBridge::started, this, [this] { sendHello(); });

    connect(m_bridge, &PythonBridge::messageReceived, this, [this](const Envelope& envelope) {
        if (m_phase == Phase::Handshaking && envelope.type == kMsgRuntimeReady
            && envelope.requestId == m_helloRequestId) {
            m_handshakeTimer.stop();

            // 能力协商：Worker 未提供宿主必需的能力 → 降级 native，不半吊子运行
            const QStringList offered =
                envelope.payload.value(QStringLiteral("capabilities")).toVariant().toStringList();
            QStringList missing;
            for (const QString& required : m_requiredCapabilities) {
                if (!offered.contains(required)) {
                    missing << required;
                }
            }
            if (!missing.isEmpty()) {
                m_phase = Phase::Stopping;
                emit downgraded(QStringLiteral("capability_missing:%1").arg(missing.join(u',')));
                stop();
                return;
            }
            m_phase = Phase::Ready;
            emit ready(offered);
        }
        if (m_phase == Phase::Ready || m_phase == Phase::Handshaking) {
            emit messageReceived(envelope);
        }
    });

    connect(m_bridge, &PythonBridge::channelBroken, this,
            [this](const QString& reason) { handleFailure(reason); });
    connect(m_bridge, &PythonBridge::processGone, this,
            [this](const QString& reason) { handleFailure(reason); });

    connect(m_bridge, &PythonBridge::finished, this, [this] {
        if (m_phase == Phase::Stopping) {
            m_graceTimer.stop();
            m_phase = Phase::Idle;
            emit stopped(QStringLiteral("shutdown_complete"));
            return;
        }
        // 非主动停止的退出（EOF、自杀、被杀）都按故障处理；
    // Recovering 阶段的 finished（我们自己 abort 引发的）不算新故障
        if (m_phase == Phase::Ready || m_phase == Phase::Handshaking) {
            handleFailure(QStringLiteral("worker_exited"));
        }
    });
}

void PythonServiceSupervisor::configure(const QString& program, const QStringList& arguments,
                                        const QStringList& requiredCapabilities)
{
    m_program = program;
    m_arguments = arguments;
    m_requiredCapabilities = requiredCapabilities;
}

void PythonServiceSupervisor::start()
{
    if (m_phase != Phase::Idle) {
        return;
    }
    m_windowClock.start();
    m_crashTimes.clear();
    beginHandshake();
}

void PythonServiceSupervisor::beginHandshake()
{
    m_phase = Phase::Handshaking;
    m_bridge->beginSession();
    m_bridge->start(m_program, m_arguments);
    m_handshakeTimer.start(kHandshakeTimeoutMs);
}

void PythonServiceSupervisor::sendHello()
{
    QJsonObject payload;
    payload.insert(QStringLiteral("host_version"), QCoreApplication::applicationVersion());
    payload.insert(QStringLiteral("required_capabilities"), QJsonArray::fromStringList(m_requiredCapabilities));
    m_helloRequestId = m_bridge->sendRequest(kMsgRuntimeHello, payload);
}

void PythonServiceSupervisor::stop()
{
    if (m_phase == Phase::Idle || m_phase == Phase::Stopping) {
        return;
    }
    m_phase = Phase::Stopping;
    m_handshakeTimer.stop();
    m_restartTimer.stop();

    // runtime.shutdown 是 event：进程退出本身就是回执，不留悬挂的 request_id
    QJsonObject payload;
    payload.insert(QStringLiteral("timeout_ms"), kShutdownGraceMs);
    m_bridge->sendEvent(kMsgRuntimeShutdown, payload);
    m_graceTimer.start(kShutdownGraceMs);

    // 进程若早已退出（比如崩溃路径顺带走到 stop），finished 不会再发，主动收尾
    if (!m_bridge->isRunning()) {
        m_graceTimer.stop();
        m_phase = Phase::Idle;
        emit stopped(QStringLiteral("shutdown_complete"));
    }
}

void PythonServiceSupervisor::handleFailure(const QString& reason)
{
    if (m_phase == Phase::Idle || m_phase == Phase::Stopping) {
        return;
    }

    if (reason == QStringLiteral("failed_to_start")) {
        // 启动失败（解释器/入口缺失）：重试也不会成功，立即放弃并让宿主回退 native
        m_handshakeTimer.stop();
        m_restartTimer.stop();
        m_phase = Phase::Idle;
        emit stopped(QStringLiteral("failed_to_start"));
        return;
    }

    // 优雅关闭路径上的自杀式 kill（超时兜底/能力降级）不算故障
    if (m_phase == Phase::Ready || m_phase == Phase::Handshaking) {
        m_handshakeTimer.stop();
        m_restartTimer.stop();
        // 进入恢复期：恢复期内来自同一次故障的后续信号（abort 引发的 finished 等）
        // 一律忽略，避免重复计数
        m_phase = Phase::Recovering;

        const qint64 now = m_windowClock.elapsed();
        // 只统计窗口内的崩溃：滑出 60s 的旧记录作废
        m_crashTimes.erase(std::remove_if(m_crashTimes.begin(), m_crashTimes.end(),
                                          [now](qint64 t) { return now - t > kRestartWindowMs; }),
                           m_crashTimes.end());

        if (m_crashTimes.size() >= kMaxRestartsPerWindow) {
            m_phase = Phase::Idle;
            emit stopped(QStringLiteral("restart_budget_exhausted:%1").arg(reason));
            return;
        }

        m_crashTimes.append(now);
        m_bridge->abort();
        const int backoffIndex = qMin<int>(m_crashTimes.size() - 1, 2);
        m_restartTimer.start(kRestartBackoffMs[backoffIndex]);
    }
}

} // namespace Pet::Services
