#pragma once

#include "BaseAgentMonitor.hpp"

#include <QHash>
#include <QSqlDatabase>
#include <QString>

namespace Pet::Services {

// 1:1 移植自 Python AntigravityMonitor。
//
// 支持双重实时同步：
// 1. 官方生命周期 Hooks (~/.gemini/config/hooks.json)，事件直接写入
//    agent-events/antigravity.jsonl；
// 2. 活动会话 SQLite 数据库检查 (~/.gemini/antigravity-ide/conversations/*.db)，
//    实时捕获当前步骤与工具执行；
// 3. transcript.jsonl 增量 tail 兜底。
class AntigravityMonitor : public BaseAgentMonitor {
    Q_OBJECT
public:
    explicit AntigravityMonitor(const QString& configDir, QObject* parent = nullptr,
                                const QString& baseDir = {});
    ~AntigravityMonitor() override;

    void start() override;

    // Python 类常量
    static QString hookFlag();
    static QString hookKey();
    static QString legacyHookKey();

    static QString hooksPath();
    // 把 Antigravity 事件写入脚本落地到 eventsFile 同目录，返回脚本路径。
    static QString ensureHookScript(const QString& eventsFile);
    static bool installHooks(const QString& eventsFile, const QString& hooksPathOverride = {});
    static bool uninstallHooks(const QString& hooksPathOverride = {});

protected:
    void poll() override;

private:
    void pollActiveDb();
    bool ensureDatabaseOpen(const QString& path);
    void closeDatabase();
    static QString extractToolFromMetadata(const QByteArray& metadata);

    QString m_antigravityBase; // .../antigravity-ide/brain
    QHash<QString, Core::ByteOffsetTailer> m_tailers;
    double m_scanInterval = 30.0;
    double m_lastScan = 0.0;

    QString m_dbLastPath;
    qint64 m_dbLastIndex = -1;
    bool m_hasDbLast = false;
    QString m_dbLastState = QStringLiteral("idle");

    QString m_dbConnectionName = QStringLiteral("pet-antigravity-steps");
    QSqlDatabase m_db;
    QString m_dbOpenPath;
};

} // namespace Pet::Services
