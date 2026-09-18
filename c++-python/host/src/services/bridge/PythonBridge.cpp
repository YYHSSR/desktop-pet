#include "services/bridge/PythonBridge.hpp"

#include <QDebug>
#include <QUuid>

#include <algorithm>

namespace Pet::Services {

namespace {
constexpr qsizetype kMaxQueueMessages = kMaxOutboundQueueMessages;
constexpr qint64 kMaxQueueBytes = kMaxOutboundQueueBytes;
} // namespace

PythonBridge::PythonBridge(QObject* parent) : QObject(parent)
{
    m_process = new QProcess(this);
    m_job = new JobObjectGuard();

    // 一次 readyRead 不等于一条消息：字节交给分帧器，信封逐条上抛
    connect(m_process, &QProcess::readyRead, this, [this] {
        const QByteArray data = m_process->readAll();
        if (data.isEmpty()) {
            return;
        }
        const QList<Envelope> delivered = m_codec.feed(data);
        if (m_codec.aborted) {
            // 缓冲超限且无换行：本次协议会话已损坏，唯一安全的动作是重启进程
            markUnhealthy(QStringLiteral("framing_aborted"));
            m_process->kill();
            return;
        }
        for (const Envelope& envelope : delivered) {
            if (envelope.type == kMsgRuntimePong) {
                resetHeartbeat();
            }
            emit messageReceived(envelope);
        }
    });

    connect(m_process, &QProcess::started, this, [this] {
        m_job->attach(static_cast<quint64>(m_process->processId()));
        flushQueue();
        emit started();
    });

    connect(m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError error) {
        if (error == QProcess::FailedToStart) {
            emit processGone(QStringLiteral("failed_to_start"));
        } else if (error == QProcess::Crashed) {
            emit processGone(QStringLiteral("crashed"));
        } else if (error == QProcess::Timedout || error == QProcess::WriteError
                   || error == QProcess::ReadError) {
            markUnhealthy(QStringLiteral("process_io_error"));
        }
        // UnknownError 交给 finished 信号统一处理，避免双报
    });

    // Worker stderr：诊断流，不属于协议通道，原样上抛（宿主落盘）
    connect(m_process, &QProcess::readyReadStandardError, this, [this] {
        const QByteArray text = m_process->readAllStandardError();
        if (!text.isEmpty()) {
            emit stderrReceived(QString::fromUtf8(text));
        }
    });

    // 崩溃统一由 errorOccurred(Crashed) 上报（kill 也走这条），finished 不重复发，
    // 避免同一次退出让上层重启逻辑走两遍
    connect(m_process, &QProcess::finished, this, [this](int exitCode, QProcess::ExitStatus exitStatus) {
        Q_UNUSED(exitCode);
        Q_UNUSED(exitStatus);
        m_heartbeatTimer.stop();
        emit finished();
    });

    m_heartbeatTimer.setInterval(kHeartbeatIntervalMs);
    connect(&m_heartbeatTimer, &QTimer::timeout, this, &PythonBridge::checkHeartbeat);
}

PythonBridge::~PythonBridge()
{
    abort();
    delete m_job;
}

void PythonBridge::beginSession()
{
    // session_id 规则：^[A-Za-z0-9_.:-]+$，8..64 字符；去掉花括号的 UUID 符合
    m_sessionId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    m_seq = 0;
    m_requestCounter = 0;
    m_queue.clear();
    m_queueBytes = 0;
    m_healthy = true;
    m_codec = ProtocolCodec{};
    m_heartbeatMisses = 0;
    m_heartbeatTimer.stop();
}

void PythonBridge::setEnvironmentPath(const QString& pythonPathDir)
{
    m_pythonPathDir = pythonPathDir;
}

void PythonBridge::start(const QString& program, const QStringList& arguments)
{
    if (m_sessionId.isEmpty()) {
        beginSession();
    }
    if (!m_pythonPathDir.isEmpty()) {
        // 脚本直启包入口：把包的父目录放到 PYTHONPATH 最前（不破坏既有环境）
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        const QString existing = env.value(QStringLiteral("PYTHONPATH"));
        env.insert(QStringLiteral("PYTHONPATH"),
                   existing.isEmpty() ? m_pythonPathDir : m_pythonPathDir + QLatin1Char(';') + existing);
        m_process->setProcessEnvironment(env);
    }
    m_process->setProgram(program);
    m_process->setArguments(arguments);
    m_process->start();
    m_heartbeatTimer.start();
}

void PythonBridge::abort()
{
    m_heartbeatTimer.stop();
    // 只发终止信号，绝不等待：GUI 线程禁止 waitForFinished；
    // 清理由 finished 信号与 QProcess 析构兜底完成
    if (m_process->state() != QProcess::NotRunning) {
        m_process->kill();
    }
}

bool PythonBridge::isRunning() const
{
    return m_process->state() == QProcess::Running;
}

quint64 PythonBridge::processId() const
{
    return m_process->state() == QProcess::NotRunning ? 0 : static_cast<quint64>(m_process->processId());
}

qint64 PythonBridge::nextSeq()
{
    return m_seq++;
}

void PythonBridge::sendEvent(const QString& type, const QJsonObject& payload, const QString& dedupeKey,
                             bool droppable)
{
    const Envelope envelope = makeEnvelope(Kind::Event, type, payload, QString(), m_sessionId, nextSeq());
    bool ok = false;
    QString rejection;
    const QByteArray line = encodeLine(envelope, &ok, &rejection);
    if (!ok) {
        // 自己写出的消息不合法属于程序错误：宁可丢弃也不写坏通道
        emit diagnostic(QStringLiteral("outbound event rejected: type=%1 reason=%2").arg(type, rejection));
        return;
    }
    pushLine(line, dedupeKey, droppable);
}

QString PythonBridge::sendRequest(const QString& type, const QJsonObject& payload)
{
    const QString requestId = QStringLiteral("h-%1").arg(++m_requestCounter);
    const Envelope envelope =
        makeEnvelope(Kind::Request, type, payload, requestId, m_sessionId, nextSeq());
    bool ok = false;
    QString rejection;
    const QByteArray line = encodeLine(envelope, &ok, &rejection);
    if (!ok) {
        emit diagnostic(QStringLiteral("outbound request rejected: type=%1 reason=%2").arg(type, rejection));
        return requestId;
    }
    pushLine(line, QString(), false);
    return requestId;
}

void PythonBridge::sendResponse(const QString& type, const QJsonObject& payload, const QString& requestId)
{
    const Envelope envelope =
        makeEnvelope(Kind::Response, type, payload, requestId, m_sessionId, nextSeq());
    bool ok = false;
    QString rejection;
    const QByteArray line = encodeLine(envelope, &ok, &rejection);
    if (!ok) {
        emit diagnostic(QStringLiteral("outbound response rejected: type=%1 reason=%2").arg(type, rejection));
        return;
    }
    // 回执不得静默丢弃：队列放不下就标记通道不健康，由上层重启恢复
    pushLine(line, QString(), false);
}

bool PythonBridge::pushLine(const QByteArray& line, const QString& dedupeKey, bool droppable)
{
    if (!isHealthy()) {
        return false;
    }
    const qsizetype size = line.size();

    // 可合并的普通更新：同 key 的旧条目被替换（桌宠只需要最新状态）
    if (!dedupeKey.isEmpty()) {
        for (auto it = m_queue.begin(); it != m_queue.end();) {
            if (it->dedupeKey == dedupeKey) {
                m_queueBytes -= it->size;
                it = m_queue.erase(it);
            } else {
                ++it;
            }
        }
    }

    while (m_queue.size() + 1 > kMaxQueueMessages || m_queueBytes + size > kMaxQueueBytes) {
        const auto victim = std::find_if(m_queue.begin(), m_queue.end(),
                                         [](const OutboundItem& item) { return item.droppable; });
        if (victim == m_queue.end()) {
            if (droppable) {
                return false; // 可丢消息直接丢，不计为故障
            }
            // 关键消息不得静默丢弃：标记通道不健康，让宿主重启并重新同步
            markUnhealthy(QStringLiteral("outbound_queue_overflow"));
            return false;
        }
        m_queueBytes -= victim->size;
        m_queue.erase(victim);
    }

    m_queue.append(OutboundItem{line, dedupeKey, droppable, size});
    m_queueBytes += size;
    flushQueue();
    return true;
}

void PythonBridge::flushQueue()
{
    if (!isRunning() || !isHealthy()) {
        return;
    }
    while (!m_queue.isEmpty()) {
        const qint64 written = m_process->write(m_queue.first().line);
        if (written < 0) {
            emit diagnostic(QStringLiteral("write failed: pending=%1").arg(m_queue.size()));
            markUnhealthy(QStringLiteral("write_failed"));
            return;
        }
        m_queueBytes -= m_queue.first().size;
        m_queue.removeFirst();
    }
}

void PythonBridge::markUnhealthy(const QString& reason)
{
    if (!m_healthy) {
        return;
    }
    m_healthy = false;
    emit channelBroken(reason);
}

void PythonBridge::checkHeartbeat()
{
    if (!isRunning()) {
        return;
    }
    if (m_heartbeatMisses >= kHeartbeatMissThreshold) {
        // 连续多个心跳周期无 pong：Worker 卡死。按失联处理，交给上层重启。
        markUnhealthy(QStringLiteral("heartbeat_missed"));
        m_process->kill();
        return;
    }
    ++m_heartbeatMisses;
    sendRequest(kMsgRuntimePing, {});
}

void PythonBridge::resetHeartbeat()
{
    m_heartbeatMisses = 0;
}

} // namespace Pet::Services
