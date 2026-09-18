#pragma once

#include "services/bridge/JobObjectGuard.hpp"
#include "services/bridge/ProtocolCodec.hpp"
#include "services/bridge/ProtocolMessage.hpp"

#include <QElapsedTimer>
#include <QObject>
#include <QProcess>
#include <QTimer>

#include <QHash>

namespace Pet::Services {

// 拥有 QProcess 的传输层：seq 分配、request_id 关联、出向背压、心跳与失联判定。
//
// 分帧绝不能假设一次 readyRead 等于一条消息——交给 ProtocolCodec；本类只负责
// 把字节喂进去、把信封发出去。GUI 线程绝不阻塞：只用 readyRead / finished /
// errorOccurred 信号，绝无 waitForFinished。
class PythonBridge : public QObject {
    Q_OBJECT
public:
    explicit PythonBridge(QObject* parent = nullptr);
    ~PythonBridge() override;

    // 启动 Worker 进程。program 为解释器绝对路径，arguments 以 Worker 入口结尾。
    void start(const QString& program, const QStringList& arguments);
    // 以脚本路径直启 Worker 包入口时，包的父目录必须在 PYTHONPATH 上
    // （如 .../worker/src）。留空表示 Worker 已按包安装，不改环境。
    void setEnvironmentPath(const QString& pythonPathDir);
    // 立即终止进程（崩溃恢复路径；优雅关闭走 Supervisor 的 runtime.shutdown）
    void abort();

    // 新 session：换 session_id，seq/request_id 归零、清空队列、恢复健康。
    // 必须在每次 start 前调用——旧 session 的出入向状态一个都不能带过来。
    void beginSession();

    void sendEvent(const QString& type, const QJsonObject& payload,
                   const QString& dedupeKey = QString(), bool droppable = false);
    // 返回为本请求分配的 request_id
    QString sendRequest(const QString& type, const QJsonObject& payload);
    void sendResponse(const QString& type, const QJsonObject& payload, const QString& requestId);

    // 每 session、每方向单调递增的序号
    qint64 nextSeq();

    [[nodiscard]] bool isRunning() const;
    [[nodiscard]] bool isHealthy() const { return m_healthy && !m_codec.aborted; }
    [[nodiscard]] QString sessionId() const { return m_sessionId; }
    [[nodiscard]] quint64 processId() const;

signals:
    void started();
    void messageReceived(const Pet::Services::Envelope& envelope);
    // Worker 的 stderr 诊断输出（日志，不属于协议通道），原样上抛由宿主落盘
    void stderrReceived(const QString& text);
    // 桥接层自身的异常事件（出向被拒/写失败等），供宿主落盘排查
    void diagnostic(const QString& message);
    // 通道级故障（分帧中止/写失败/队列溢出/心跳失联）：宿主应重启并重新同步快照
    void channelBroken(const QString& reason);
    // 进程级故障（启动失败/崩溃/意外退出）
    void processGone(const QString& reason);
    void finished();

private:
    struct OutboundItem {
        QByteArray line;
        QString dedupeKey;
        bool droppable = false;
        qsizetype size = 0;
    };

    bool pushLine(const QByteArray& line, const QString& dedupeKey, bool droppable);
    void flushQueue();
    void markUnhealthy(const QString& reason);
    void checkHeartbeat();
    void resetHeartbeat();

    ProtocolCodec m_codec;
    QProcess* m_process = nullptr;
    JobObjectGuard* m_job = nullptr;
    QTimer m_heartbeatTimer;

    QString m_sessionId;
    QString m_pythonPathDir;
    qint64 m_seq = 0;
    quint64 m_requestCounter = 0;
    QList<OutboundItem> m_queue;
    qint64 m_queueBytes = 0;
    bool m_healthy = true;
    int m_heartbeatMisses = 0;
};

} // namespace Pet::Services
