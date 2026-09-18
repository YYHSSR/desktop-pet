#pragma once

#include "services/bridge/PythonBridge.hpp"

#include <QObject>
#include <QTimer>
#include <QElapsedTimer>

namespace Pet::Services {

// Worker 生命周期编排：按需启动、5s 握手超时、1/2/4s 退避重启、
// 60s 窗口内最多 3 次、2s 异步优雅关闭、能力协商与降级。
//
// 宿主只在 `ready` 之后才允许把业务信号接进来；`downgraded`/`stopped`
// 意味着必须回退 native，绝不让两套来源同时驱动桌宠。
class PythonServiceSupervisor : public QObject {
    Q_OBJECT
public:
    explicit PythonServiceSupervisor(PythonBridge* bridge, QObject* parent = nullptr);

    // program = 解释器绝对路径；arguments 以 Worker 入口脚本结尾
    void configure(const QString& program, const QStringList& arguments,
                   const QStringList& requiredCapabilities);
    void start();
    // 优雅关闭：发 runtime.shutdown 后最多等 kShutdownGraceMs，超时才杀。
    // 只用事件循环 + 单次定时器等待，GUI 线程绝不阻塞。
    void stop();

    [[nodiscard]] bool isActive() const { return m_phase != Phase::Idle; }
    [[nodiscard]] bool isReady() const { return m_phase == Phase::Ready; }

signals:
    // 握手完成且能力齐备
    void ready(const QStringList& capabilities);
    // 能力不足或握手失败到无法恢复：宿主应回退 native
    void downgraded(const QString& reason);
    // 终态（重启预算耗尽 / 主动关闭完成）
    void stopped(const QString& reason);
    void messageReceived(const Pet::Services::Envelope& envelope);

private:
    enum class Phase {
        Idle,
        Handshaking,
        Ready,
        Recovering, // 已记一次故障，正在退避等待重启；期间的后续信号一律忽略
        Stopping,
    };

    void sendHello();
    void handleFailure(const QString& reason);
    void scheduleRestart();
    void beginHandshake();

    PythonBridge* m_bridge = nullptr;
    QString m_program;
    QStringList m_arguments;
    QStringList m_requiredCapabilities;

    Phase m_phase = Phase::Idle;
    QTimer m_handshakeTimer;
    QTimer m_graceTimer;
    QTimer m_restartTimer;
    QString m_helloRequestId;

    // 60s 窗口内的崩溃时刻表；窗口滑出后旧记录作废
    QElapsedTimer m_windowClock;
    QList<qint64> m_crashTimes;
};

} // namespace Pet::Services
