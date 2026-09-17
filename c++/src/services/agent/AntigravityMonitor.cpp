#include "AntigravityMonitor.hpp"

#include "core/AgentProtocol.hpp"

#include <QDateTime>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
#include <QSqlError>
#include <QSqlQuery>
#include <QVariant>

#include <algorithm>

namespace Pet::Services {
namespace {

double wallSeconds() {
    return static_cast<double>(QDateTime::currentMSecsSinceEpoch()) / 1000.0;
}

constexpr int kMaxTrackedFiles = 50;

// Python: status 2 = RUNNING, 3 = COMPLETED, 4 = ERROR/CANCELLED
constexpr int kStepRunning = 2;
constexpr int kStepCompleted = 3;
// Python: step_type 15 = PLANNER_RESPONSE（思考中）
constexpr int kStepTypePlannerResponse = 15;

// Python _ensure_hook_script 写出的 PowerShell 版本。
// Python 源码运行时用 python 解释器；打包（frozen）后回退 PowerShell——
// C++ 二进制等价于 frozen 场景，因此统一使用 PowerShell 分支。
const char* const kPowerShellHookScript = R"(param([string]$EventName = 'unknown')
$tool = ''
$state = 'working'
if ($EventName -eq 'PreInvocation') { $state = 'thinking' } elseif ($EventName -eq 'Stop') { $state = 'idle' }
if ([Console]::IsInputRedirected) {
  try {
    $raw = [Console]::In.ReadToEnd()
    if ($raw) {
      $j = $raw | ConvertFrom-Json -ErrorAction SilentlyContinue
      if ($j.toolCall -and $j.toolCall.name) { $tool = [string]$j.toolCall.name }
    }
  } catch {}
}
$file = Join-Path $PSScriptRoot 'antigravity.jsonl'
$rec = [ordered]@{ ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0; agent = 'antigravity'; state = $state; event = $EventName }
if ($tool) { $rec['tool'] = $tool }
try { Add-Content -Path $file -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
if ($EventName -eq 'PreToolUse') { '{"decision":"allow"}' } else { '{}' }
)";

QJsonObject readJsonObject(const QString& path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    QJsonParseError error{};
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        return {};
    }
    return document.object();
}

bool writeJsonObject(const QString& path, const QJsonObject& object) {
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        return false;
    }
    file.write(QJsonDocument(object).toJson(QJsonDocument::Indented));
    return file.commit();
}

QJsonObject commandHook(const QString& command) {
    return QJsonObject{
        {QStringLiteral("type"), QStringLiteral("command")},
        {QStringLiteral("command"), command},
    };
}

QJsonObject matcherHook(const QString& command) {
    return QJsonObject{
        {QStringLiteral("matcher"), QStringLiteral("*")},
        {QStringLiteral("hooks"), QJsonArray{commandHook(command)}},
    };
}

} // namespace

AntigravityMonitor::AntigravityMonitor(const QString& configDir, QObject* parent,
                                       const QString& baseDir)
    : BaseAgentMonitor(QStringLiteral("antigravity"), configDir, parent)
    , m_antigravityBase(baseDir.trimmed().isEmpty()
                            ? QDir::home().filePath(
                                  QStringLiteral(".gemini/antigravity-ide/brain"))
                            : baseDir.trimmed()) {
    setPollInterval(400); // 提高轮询刷新率，提升交互实时性
}

AntigravityMonitor::~AntigravityMonitor() {
    closeDatabase();
}

QString AntigravityMonitor::hookFlag() {
    return QStringLiteral("x-desktop-pet");
}

QString AntigravityMonitor::hookKey() {
    return QStringLiteral("desktop-pet-hook");
}

QString AntigravityMonitor::legacyHookKey() {
    return QStringLiteral("desktop-pet-hook-legacy");
}

QString AntigravityMonitor::hooksPath() {
    return QDir::home().filePath(QStringLiteral(".gemini/config/hooks.json"));
}

QString AntigravityMonitor::ensureHookScript(const QString& eventsFile) {
    const QDir directory = QFileInfo(eventsFile).absoluteDir();
    QDir().mkpath(directory.absolutePath());
    const QString scriptPath =
        directory.filePath(QStringLiteral("antigravity_event_hook.ps1"));

    QFile script(scriptPath);
    if (script.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        script.write(kPowerShellHookScript);
        script.close();
    }
    return scriptPath;
}

bool AntigravityMonitor::installHooks(const QString& eventsFile,
                                      const QString& hooksPathOverride) {
    const QString target = hooksPathOverride.trimmed().isEmpty() ? hooksPath()
                                                                : hooksPathOverride.trimmed();
    const QString script = ensureHookScript(eventsFile);
    if (script.isEmpty()) {
        return false;
    }
    if (!QDir().mkpath(QFileInfo(target).absolutePath())) {
        return false;
    }

    QJsonObject data = readJsonObject(target);

    const auto commandFor = [&script](const QString& event) {
        // Python 打包分支：powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" {event}
        return QStringLiteral(
                   "powershell -NoProfile -ExecutionPolicy Bypass -File \"%1\" %2")
            .arg(script, event);
    };

    data.remove(legacyHookKey());
    data[hookKey()] = QJsonObject{
        {QStringLiteral("PreInvocation"), QJsonArray{commandHook(commandFor(QStringLiteral("PreInvocation")))}},
        {QStringLiteral("PreToolUse"), QJsonArray{matcherHook(commandFor(QStringLiteral("PreToolUse")))}},
        {QStringLiteral("PostToolUse"), QJsonArray{matcherHook(commandFor(QStringLiteral("PostToolUse")))}},
        {QStringLiteral("Stop"), QJsonArray{commandHook(commandFor(QStringLiteral("Stop")))}},
        {hookFlag(), true},
    };
    return writeJsonObject(target, data);
}

bool AntigravityMonitor::uninstallHooks(const QString& hooksPathOverride) {
    const QString target = hooksPathOverride.trimmed().isEmpty() ? hooksPath()
                                                                : hooksPathOverride.trimmed();
    if (!QFileInfo::exists(target)) {
        return true;
    }
    const QJsonObject data = readJsonObject(target);
    bool changed = false;
    QJsonObject next = data;
    for (const QString& key : {hookKey(), legacyHookKey()}) {
        if (next.contains(key)) {
            next.remove(key);
            changed = true;
        }
    }
    if (!changed) {
        return true;
    }
    return writeJsonObject(target, next);
}

void AntigravityMonitor::start() {
    const QString script = ensureHookScript(m_eventsFile);
    if (!script.isEmpty()) {
        installHooks(m_eventsFile);
    }
    BaseAgentMonitor::start();
}

void AntigravityMonitor::closeDatabase() {
    if (m_db.isValid()) {
        if (m_db.isOpen()) {
            m_db.close();
        }
        m_db = QSqlDatabase();
    }
    if (QSqlDatabase::contains(m_dbConnectionName)) {
        QSqlDatabase::removeDatabase(m_dbConnectionName);
    }
    m_dbOpenPath.clear();
}

bool AntigravityMonitor::ensureDatabaseOpen(const QString& path) {
    if (m_db.isValid() && m_db.isOpen() && m_dbOpenPath == path) {
        return true;
    }
    closeDatabase();

    m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), m_dbConnectionName);
    m_db.setDatabaseName(path);
    // Python: sqlite3.connect(uri mode=ro, timeout=0.3)
    m_db.setConnectOptions(QStringLiteral("QSQLITE_OPEN_READONLY;QSQLITE_BUSY_TIMEOUT=40"));
    m_dbOpenPath = path;
    if (!m_db.open()) {
        closeDatabase();
        return false;
    }
    return true;
}

QString AntigravityMonitor::extractToolFromMetadata(const QByteArray& metadata) {
    if (metadata.isEmpty()) {
        return QString();
    }
    static const QList<QByteArray> known = {
        "run_command", "view_file", "replace_file_content",
        "multi_replace_file_content", "write_to_file", "grep_search",
        "search_web", "read_url_content", "browser_subagent",
        "ask_question",
    };
    for (const QByteArray& tool : known) {
        if (metadata.contains(tool)) {
            return QString::fromLatin1(tool);
        }
    }
    // Python: re.search(rb'\x12[\x03-\x20]([a-z][a-z0-9_]{2,30})', raw)
    static const QRegularExpression pattern(
        QStringLiteral("\u0012[\\x03-\\x20]([a-z][a-z0-9_]{2,30})"));
    const QRegularExpressionMatch match =
        pattern.match(QString::fromLatin1(metadata));
    if (match.hasMatch()) {
        return match.captured(1);
    }
    return QString();
}

void AntigravityMonitor::pollActiveDb() {
    // Python: conv_dir = self.antigravity_base.parent / "conversations"
    const QDir conversations(QDir(m_antigravityBase).absoluteFilePath(
        QStringLiteral("../conversations")));
    if (!conversations.exists()) {
        return;
    }

    const double now = wallSeconds();
    QList<QPair<qint64, QString>> candidates;
    const QStringList entries =
        conversations.entryList({QStringLiteral("*.db")}, QDir::Files, QDir::NoSort);
    for (const QString& entry : entries) {
        const QFileInfo info(conversations.absoluteFilePath(entry));
        if (!info.isFile()) {
            continue;
        }
        qint64 modified = info.lastModified().toMSecsSinceEpoch();
        // 检查 WAL 文件，以防 SQLite WAL 模式下 .db mtime 滞后
        const QFileInfo wal(info.absoluteFilePath() + QStringLiteral("-wal"));
        if (wal.isFile()) {
            modified = std::max(modified, wal.lastModified().toMSecsSinceEpoch());
        }
        if (QDateTime::currentMSecsSinceEpoch() - modified <= 600LL * 1000) {
            candidates.append({modified, info.absoluteFilePath()});
        }
    }

    if (candidates.isEmpty()) {
        if (m_dbLastState == QLatin1String("thinking")
            || m_dbLastState == QLatin1String("working")) {
            emit stateChanged(m_agentKey, QStringLiteral("idle"));
            m_dbLastState = QStringLiteral("idle");
        }
        return;
    }

    std::sort(candidates.begin(), candidates.end(),
              [](const QPair<qint64, QString>& left, const QPair<qint64, QString>& right) {
                  return left.first > right.first;
              });
    const QString latestDb = candidates.first().second;

    if (!ensureDatabaseOpen(latestDb)) {
        return;
    }

    qint64 index = -1;
    int stepType = -1;
    int status = -1;
    QByteArray metadata;
    bool hasRow = false;
    QSqlQuery query(m_db);
    if (query.exec(QStringLiteral(
            "SELECT idx, step_type, status, metadata FROM steps ORDER BY idx DESC LIMIT 1"))) {
        if (query.next()) {
            hasRow = true;
            index = query.value(0).toLongLong();
            stepType = query.value(1).toInt();
            status = query.value(2).toInt();
            metadata = query.value(3).toByteArray();
        }
    }
    if (!hasRow) {
        return;
    }

    if (status == kStepRunning) {
        const QString targetState = (stepType == kStepTypePlannerResponse)
            ? QStringLiteral("thinking")
            : QStringLiteral("working");
        const QString tool = extractToolFromMetadata(metadata);
        if (!tool.isEmpty()) {
            emit activity(m_agentKey, tool);
        }
        emit stateChanged(m_agentKey, targetState);
        m_hasDbLast = true;
        m_dbLastPath = latestDb;
        m_dbLastIndex = index;
        m_dbLastState = targetState;
        return;
    }

    const bool wasBusy = m_dbLastState == QLatin1String("thinking")
        || m_dbLastState == QLatin1String("working");
    if (status == kStepCompleted || (status != kStepRunning && wasBusy)) {
        const bool lastWasRunning = m_hasDbLast && m_dbLastIndex >= 0;
        if (lastWasRunning || wasBusy) {
            emit stateChanged(m_agentKey, QStringLiteral("idle"));
            m_hasDbLast = true;
            m_dbLastPath = latestDb;
            m_dbLastIndex = index;
            m_dbLastState = QStringLiteral("idle");
        }
    }
}

void AntigravityMonitor::poll() {
    // 首先检查统一 jsonl 通道（hooks 注入事件在此读取）
    BaseAgentMonitor::poll();

    // 检查活动数据库（即使 hooks 尚未被 Antigravity 重载也能感知）
    pollActiveDb();

    if (!QFileInfo(m_antigravityBase).isDir()) {
        return;
    }

    const double now = wallSeconds();
    if (now - m_lastScan >= m_scanInterval) {
        m_lastScan = now;
        const qint64 oneDayAgoMs = QDateTime::currentMSecsSinceEpoch() - 86400LL * 1000;

        QList<QPair<qint64, QString>> files;
        QDirIterator iterator(m_antigravityBase, QDir::Files, QDirIterator::Subdirectories);
        while (iterator.hasNext()) {
            const QString path = iterator.next();
            if (QFileInfo(path).fileName() != QLatin1String("transcript.jsonl")) {
                continue;
            }
            const QFileInfo info(path);
            if (!info.isFile() || info.lastModified().toMSecsSinceEpoch() < oneDayAgoMs) {
                continue;
            }
            files.append({info.lastModified().toMSecsSinceEpoch(), info.absoluteFilePath()});
        }
        std::sort(files.begin(), files.end(),
                  [](const QPair<qint64, QString>& left, const QPair<qint64, QString>& right) {
                      return left.first > right.first;
                  });
        if (files.size() > kMaxTrackedFiles) {
            files = files.mid(0, kMaxTrackedFiles);
        }

        QSet<QString> candidates;
        for (const auto& item : files) {
            candidates.insert(item.second);
        }
        for (auto it = m_tailers.begin(); it != m_tailers.end();) {
            if (!candidates.contains(it.key())) {
                it = m_tailers.erase(it);
            } else {
                ++it;
            }
        }
        for (const QString& path : candidates) {
            if (!m_tailers.contains(path)) {
                m_tailers.insert(path, Core::ByteOffsetTailer(path));
            }
        }
    }

    for (auto it = m_tailers.begin(); it != m_tailers.end(); ++it) {
        const QStringList lines = it.value().readNewLines();
        for (const QString& line : lines) {
            QJsonParseError error{};
            const QJsonDocument document = QJsonDocument::fromJson(line.toUtf8(), &error);
            if (error.error != QJsonParseError::NoError || !document.isObject()) {
                continue;
            }
            const QJsonObject data = document.object();
            const QString tool = Core::antigravityEventTool(data);
            if (!tool.isEmpty()) {
                emit activity(m_agentKey, tool);
            }
            const QString normalized = Core::antigravityEventState(data);
            if (normalized.isEmpty()) {
                continue;
            }
            emit stateChanged(m_agentKey, normalized);
        }
    }
}

} // namespace Pet::Services
