#pragma once

#include "IAgentMonitor.hpp"
#include "core/ByteOffsetTailer.hpp"

#include <QObject>
#include <QString>
#include <QTimer>

namespace Pet::Services {

// 1:1 移植自 Python pet/services/agent_link.py 的 BaseAgentMonitor。
//
// 统一协议事件文件：<配置目录>/agent-events/<agentKey>.jsonl。
// 每行一个 JSON 对象，字段 event / state / tool 走 normalizeEventState 归一化。
class BaseAgentMonitor : public QObject, public IAgentMonitor {
    Q_OBJECT
public:
    BaseAgentMonitor(const QString& agentKey, const QString& configDir, QObject* parent = nullptr);

    [[nodiscard]] QString agentKey() const override { return m_agentKey; }
    [[nodiscard]] bool isRunning() const override { return m_running && !m_paused; }
    [[nodiscard]] bool started() const override { return m_running; }
    [[nodiscard]] QObject* asObject() override { return this; }
    [[nodiscard]] const QString& eventsFile() const noexcept { return m_eventsFile; }

    void start() override;
    void stop() override;
    void pause() override;
    void resume() override;

signals:
    void stateChanged(const QString& agentKey, const QString& state);
    // 过程汇报用，仅事件带工具名时发出
    void activity(const QString& agentKey, const QString& tool);

protected:
    virtual void poll();
    void setPollInterval(int milliseconds);

    QString m_agentKey;
    QString m_configDir;
    QString m_eventsDir;
    QString m_eventsFile;
    Core::ByteOffsetTailer m_tailer;
    QTimer* m_timer = nullptr;
    bool m_running = false;
    bool m_paused = false;
};

} // namespace Pet::Services
