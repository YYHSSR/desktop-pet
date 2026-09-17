#include "BaseAgentMonitor.hpp"

#include "core/AgentProtocol.hpp"

#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>

namespace Pet::Services {

BaseAgentMonitor::BaseAgentMonitor(const QString& agentKey, const QString& configDir, QObject* parent)
    : QObject(parent)
    , m_agentKey(agentKey)
    , m_configDir(configDir) {
    m_eventsDir = QDir(m_configDir).filePath(QStringLiteral("agent-events"));
    m_eventsFile = QDir(m_eventsDir).filePath(agentKey + QStringLiteral(".jsonl"));
    m_tailer.setFilePath(m_eventsFile);

    m_timer = new QTimer(this);
    m_timer->setInterval(1500);
    connect(m_timer, &QTimer::timeout, this, &BaseAgentMonitor::poll);
}

void BaseAgentMonitor::setPollInterval(int milliseconds) {
    m_timer->setInterval(milliseconds);
}

void BaseAgentMonitor::start() {
    m_running = true;
    m_paused = false;
    QDir().mkpath(m_eventsDir);
    m_tailer.reset();
    if (!m_timer->isActive()) {
        m_timer->start();
    }
}

void BaseAgentMonitor::stop() {
    m_running = false;
    m_paused = false;
    m_timer->stop();
}

void BaseAgentMonitor::pause() {
    if (m_running) {
        m_paused = true;
        m_timer->stop();
    }
}

void BaseAgentMonitor::resume() {
    if (m_running && m_paused) {
        m_paused = false;
        m_timer->start();
    }
}

void BaseAgentMonitor::poll() {
    const QStringList lines = m_tailer.readNewLines();
    for (const QString& line : lines) {
        QJsonParseError error{};
        const QJsonDocument document = QJsonDocument::fromJson(line.toUtf8(), &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) {
            continue;
        }
        const QJsonObject data = document.object();

        const QString tool = data.value(QStringLiteral("tool")).toString().trimmed();
        if (!tool.isEmpty()) {
            emit activity(m_agentKey, tool);
        }
        const QString normalized = Core::normalizeEventState(
            data.value(QStringLiteral("event")).toString(),
            data.value(QStringLiteral("state")).toString());
        if (normalized.isEmpty()) {
            continue; // 不认识的事件类型：忽略，不误报为 working
        }
        emit stateChanged(m_agentKey, normalized);
    }
}

} // namespace Pet::Services
