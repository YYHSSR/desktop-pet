#pragma once

#include "CodexStatusReader.hpp"
#include "IAgentMonitor.hpp"

#include <QFuture>
#include <QMetaType>
#include <QObject>
#include <QString>
#include <QThreadPool>
#include <QTimer>

#include <memory>

namespace Pet::Services {

// 1:1 移植自 Python pet/services/chatgpt_monitor.py。
//
// GUI 线程门面：本机 Codex 读取在唯一的、惰性创建的 worker 上执行，
// 保证既不阻塞界面，也永不写入 Codex 数据库。
class ChatGptMonitor : public QObject, public IAgentMonitor {
    Q_OBJECT
public:
    explicit ChatGptMonitor(const QString& configDir, QObject* parent = nullptr);
    ~ChatGptMonitor() override;

    [[nodiscard]] QString agentKey() const override { return m_agentKey; }
    [[nodiscard]] bool isRunning() const override { return m_running && !m_paused; }
    [[nodiscard]] bool started() const override { return m_running; }
    [[nodiscard]] QObject* asObject() override { return this; }
    [[nodiscard]] const CodexSnapshot& snapshot() const noexcept { return m_snapshot; }

    void start() override;
    void stop() override;
    void pause() override;
    void resume() override;

signals:
    void stateChanged(const QString& agentKey, const QString& state);
    void activity(const QString& agentKey, const QString& tool);
    void snapshotChanged(const CodexSnapshot& snapshot);
    void taskChanged();

private slots:
    void poll();

private:
    void publish(const CodexSnapshot& snapshot);
    void disposeWorker();

    QString m_agentKey = QStringLiteral("chatgpt");
    QString m_configDir;
    std::unique_ptr<CodexStatusReader> m_reader;
    std::unique_ptr<QThreadPool> m_pool;
    QTimer* m_timer = nullptr;

    bool m_running = false;
    bool m_paused = false;
    int m_generation = 0;
    int m_requestGeneration = 0;
    QFuture<CodexSnapshot> m_future;
    bool m_hasFuture = false;
    CodexSnapshot m_snapshot;
};

} // namespace Pet::Services

Q_DECLARE_METATYPE(Pet::Services::CodexSnapshot)
