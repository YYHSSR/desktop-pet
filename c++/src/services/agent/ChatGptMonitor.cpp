#include "ChatGptMonitor.hpp"

#include <QtConcurrent/QtConcurrentRun>

namespace Pet::Services {

ChatGptMonitor::ChatGptMonitor(const QString& configDir, QObject* parent)
    : QObject(parent)
    , m_configDir(configDir)
    , m_reader(std::make_unique<CodexStatusReader>())
    , m_timer(new QTimer(this)) {
    qRegisterMetaType<CodexSnapshot>("Pet::Services::CodexSnapshot");
    m_timer->setInterval(250);
    connect(m_timer, &QTimer::timeout, this, &ChatGptMonitor::poll);
}

ChatGptMonitor::~ChatGptMonitor() {
    disposeWorker();
}

void ChatGptMonitor::disposeWorker() {
    if (m_pool) {
        m_pool->clear(); // 丢弃尚未开始的任务
        m_pool.reset();  // 析构等待进行中的任务（有界的只读查询）
    }
    m_hasFuture = false;
    m_future = QFuture<CodexSnapshot>();
}

void ChatGptMonitor::start() {
    if (m_running) {
        return;
    }
    m_running = true;
    m_paused = false;
    m_generation++;
    m_pool = std::make_unique<QThreadPool>();
    m_pool->setMaxThreadCount(1); // 单个 worker：与 Python max_workers=1 等价
    m_timer->start(250);
    poll();
}

void ChatGptMonitor::stop() {
    m_running = false;
    m_paused = false;
    m_generation++;
    m_timer->stop();
    disposeWorker();
    m_snapshot = CodexSnapshot();
}

void ChatGptMonitor::pause() {
    if (m_running) {
        m_paused = true;
        m_generation++;
        m_timer->stop();
    }
}

void ChatGptMonitor::resume() {
    if (m_running && m_paused) {
        m_paused = false;
        m_generation++;
        m_timer->start(250);
    }
}

void ChatGptMonitor::poll() {
    if (!isRunning()) {
        return;
    }

    if (m_hasFuture) {
        if (!m_future.isFinished()) {
            return; // 上一轮仍在读，保持等待
        }
        CodexSnapshot snapshot;
        if (m_future.resultCount() > 0) {
            snapshot = m_future.result();
        } else {
            snapshot = CodexSnapshot{QStringLiteral("unavailable"), {}, {}, {}, {}, 0,
                                     QStringLiteral("监听暂不可用，稍后自动重试")};
        }
        m_hasFuture = false;
        m_future = QFuture<CodexSnapshot>();
        if (m_requestGeneration == m_generation) {
            publish(snapshot);
        }
    }

    if (m_pool) {
        m_requestGeneration = m_generation;
        CodexStatusReader* reader = m_reader.get();
        m_future = QtConcurrent::run(m_pool.get(), [reader]() {
            // CodexStatusReader 内部已把 sqlite/OS 异常折叠为「暂不可读」快照
            return reader->read();
        });
        m_hasFuture = true;
    }
}

void ChatGptMonitor::publish(const CodexSnapshot& snapshot) {
    const CodexSnapshot previous = m_snapshot;
    const bool changedTask =
        (previous.threadId != snapshot.threadId) || (previous.turnId != snapshot.turnId);

    m_snapshot = snapshot;

    const bool busy = snapshot.state == QLatin1String("thinking")
        || snapshot.state == QLatin1String("working")
        || snapshot.state == QLatin1String("attention");
    m_timer->setInterval(busy ? 250 : 1000);

    if (snapshot == previous) {
        return;
    }
    if (changedTask) {
        emit taskChanged();
    }
    emit snapshotChanged(snapshot);

    // 基线已完成任务与切换会话都不应庆祝旧历史：
    // 只有被跟踪回合上的真实状态迁移才算完成。
    if (!changedTask || busy || snapshot.state == QLatin1String("unavailable")) {
        emit stateChanged(m_agentKey, snapshot.state);
    }
    if (!snapshot.tool.isEmpty()
        && (snapshot.itemId != previous.itemId || snapshot.tool != previous.tool)) {
        emit activity(m_agentKey, snapshot.tool);
    }
}

} // namespace Pet::Services
