#include "CodexStatusReader.hpp"

#include <QDir>
#include <QFileInfo>
#include <QHash>
#include <QRegularExpression>
#include <QSet>
#include <QSqlDatabase>
#include <QSqlError>
#include <QSqlQuery>
#include <QSqlRecord>
#include <QUuid>
#include <QVariant>

namespace Pet::Services {
namespace {

QString defaultHome() {
    const QString fromEnv = QString::fromLocal8Bit(qgetenv("CODEX_HOME")).trimmed();
    if (!fromEnv.isEmpty()) {
        return fromEnv;
    }
    return QDir::home().filePath(QStringLiteral(".codex"));
}

// Python re.fullmatch(r'[A-Za-z0-9-]{1,100}', candidate)
bool isRunId(const QString& value) {
    static const QRegularExpression pattern(QStringLiteral("^[A-Za-z0-9-]{1,100}$"));
    return pattern.match(value).hasMatch();
}

// Python CodexStatusReader._history_id
QString historyId(const QString& publicId, const QVariant& rolloutPath) {
    const QString path = rolloutPath.toString();
    if (path.isEmpty()) {
        return publicId;
    }
    const QString stem = QFileInfo(path).completeBaseName();
    const int separator = stem.lastIndexOf(QLatin1Char('_'));
    const QString candidate = (separator >= 0) ? stem.mid(separator + 1) : stem;
    if (isRunId(candidate) && candidate != stem) {
        return candidate;
    }
    return publicId;
}

// 以只读方式打开并使用后立即释放连接，绝不写入。
class ScopedReadOnlyDb {
public:
    ScopedReadOnlyDb(const QString& connectionName, const QString& path)
        : m_name(connectionName) {
        m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), m_name);
        m_db.setDatabaseName(path);
        // Python: sqlite3.connect(uri mode=ro) + PRAGMA query_only=ON + 短超时
        m_db.setConnectOptions(QStringLiteral("QSQLITE_OPEN_READONLY;QSQLITE_BUSY_TIMEOUT=40"));
        m_open = m_db.open();
    }

    ~ScopedReadOnlyDb() {
        if (m_db.isOpen()) {
            m_db.close();
        }
        m_db = QSqlDatabase();
        QSqlDatabase::removeDatabase(m_name);
    }

    ScopedReadOnlyDb(const ScopedReadOnlyDb&) = delete;
    ScopedReadOnlyDb& operator=(const ScopedReadOnlyDb&) = delete;

    [[nodiscard]] bool isOpen() const { return m_open; }
    [[nodiscard]] QSqlDatabase& db() { return m_db; }

private:
    QString m_name;
    QSqlDatabase m_db;
    bool m_open = false;
};

CodexSnapshot unavailableSnapshot() {
    return CodexSnapshot();
}

CodexSnapshot unreadableSnapshot() {
    return CodexSnapshot{QStringLiteral("unavailable"), {}, {}, {}, {}, 0,
                         QStringLiteral("任务记录暂不可读或版本不兼容；稍后自动重试")};
}

} // namespace

CodexStatusReader::CodexStatusReader(const QString& home)
    : m_home(home.trimmed().isEmpty() ? defaultHome() : home.trimmed()) {}

QString CodexStatusReader::databasePath(const QString& prefix) const {
    const QDir homeDir(m_home);
    if (!homeDir.exists()) {
        return QString();
    }
    const QStringList filters{QStringLiteral("%1_*.sqlite").arg(prefix)};
    const QStringList entries = homeDir.entryList(filters, QDir::Files, QDir::NoSort);

    QString bestPath;
    qint64 bestSuffix = -1;
    for (const QString& entry : entries) {
        QString suffix = QFileInfo(entry).completeBaseName();
        suffix.remove(0, prefix.size() + 1);
        bool isNumber = false;
        const qint64 value = suffix.toLongLong(&isNumber);
        if (!isNumber || suffix.isEmpty()) {
            continue;
        }
        const QString path = homeDir.absoluteFilePath(entry);
        if (!QFileInfo(path).isFile()) {
            continue;
        }
        if (value > bestSuffix) {
            bestSuffix = value;
            bestPath = path;
        }
    }
    return bestPath;
}

CodexSnapshot CodexStatusReader::read(const QString& threadId) const {
    // Python read()：任何 sqlite/OS/类型错误都退化为「暂不可读」快照
    const CodexSnapshot snapshot = readInternal(threadId);
    return snapshot;
}

CodexSnapshot CodexStatusReader::readInternal(const QString& threadId) const {
    const QString historyPath = databasePath(QStringLiteral("thread_history"));
    const QString statePath = databasePath(QStringLiteral("state"));
    if (historyPath.isEmpty() || statePath.isEmpty()) {
        return unavailableSnapshot();
    }

    const QString token = QUuid::createUuid().toString(QUuid::WithoutBraces);

    // ---- 阶段一：从 state 库列出候选对话 ----
    QList<QPair<QString, QString>> rows; // (public_id, rollout_path)
    {
        ScopedReadOnlyDb state(QStringLiteral("codex-state-") + token, statePath);
        if (!state.isOpen()) {
            return unreadableSnapshot();
        }

        QSet<QString> columns;
        QSqlQuery columnsQuery(state.db());
        if (columnsQuery.exec(QStringLiteral("PRAGMA table_info(threads)"))) {
            while (columnsQuery.next()) {
                columns.insert(columnsQuery.value(1).toString());
            }
        } else {
            return unreadableSnapshot();
        }

        QString where = QStringLiteral("archived=0");
        if (columns.contains(QStringLiteral("agent_path"))) {
            where += QStringLiteral(
                " AND (agent_path IS NULL OR agent_path='' OR agent_path='/root')");
        }

        QSqlQuery query(state.db());
        bool prepared = false;
        if (!threadId.isEmpty()) {
            prepared = query.prepare(
                QStringLiteral("SELECT id,rollout_path FROM threads WHERE %1 "
                               "AND (id=? OR rollout_path LIKE ?) LIMIT 2")
                    .arg(where));
            if (prepared) {
                query.addBindValue(threadId);
                query.addBindValue(QStringLiteral("%_%1.jsonl").arg(threadId));
            }
        } else {
            const QString order = columns.contains(QStringLiteral("updated_at"))
                ? QStringLiteral("updated_at DESC")
                : QStringLiteral("rowid DESC");
            prepared = query.prepare(
                QStringLiteral("SELECT id,rollout_path FROM threads WHERE %1 ORDER BY %2 LIMIT 64")
                    .arg(where, order));
        }
        if (!prepared || !query.exec()) {
            return unreadableSnapshot();
        }
        while (query.next()) {
            rows.append({query.value(0).toString(), query.value(1).toString()});
        }
    }

    QHash<QString, QString> idMap; // history_id -> public_id
    for (const auto& row : rows) {
        idMap.insert(historyId(row.first, row.second), row.first);
    }
    if (idMap.isEmpty()) {
        return CodexSnapshot{QStringLiteral("unavailable"), {}, {}, {}, {}, 0,
                             threadId.isEmpty() ? QStringLiteral("尚无本机对话")
                                                : QStringLiteral("固定对话不存在或已归档")};
    }

    // ---- 阶段二：从 thread_history 库取最新回合 ----
    QString session;
    QString turn;
    QString status;
    qint64 startedAt = 0;
    bool hasTurn = false;
    {
        ScopedReadOnlyDb history(QStringLiteral("codex-history-") + token, historyPath);
        if (!history.isOpen()) {
            return unreadableSnapshot();
        }

        const QStringList historyIds = idMap.keys();
        QStringList placeholders;
        placeholders.reserve(historyIds.size());
        for (int i = 0; i < historyIds.size(); ++i) {
            placeholders.append(QStringLiteral("?"));
        }

        QSqlQuery query(history.db());
        if (!query.prepare(
                QStringLiteral("SELECT thread_id,turn_id,status,started_at FROM thread_turns "
                               "WHERE thread_id IN (%1) "
                               "ORDER BY started_at DESC,turn_id DESC LIMIT 1")
                    .arg(placeholders.join(QLatin1Char(','))))) {
            return unreadableSnapshot();
        }
        for (const QString& id : historyIds) {
            query.addBindValue(id);
        }
        if (!query.exec()) {
            return unreadableSnapshot();
        }
        if (query.next()) {
            hasTurn = true;
            session = query.value(0).toString();
            turn = query.value(1).toString();
            status = query.value(2).toString();
            startedAt = query.value(3).toLongLong();
        }
    }

    if (!hasTurn) {
        return CodexSnapshot{QStringLiteral("unavailable"), {}, {}, {}, {}, 0,
                             QStringLiteral("尚无可监听的任务回合")};
    }

    const QString publicThreadId = idMap.value(session, session);

    // ---- 阶段三：终态直接返回，否则读取最新 item 元数据 ----
    if (status == QLatin1String("completed") || status == QLatin1String("interrupted")
        || status == QLatin1String("failed") || status == QLatin1String("error")) {
        CodexSnapshot snapshot;
        snapshot.threadId = publicThreadId;
        snapshot.turnId = turn;
        snapshot.startedAt = startedAt;
        if (status == QLatin1String("completed")) {
            snapshot.state = QStringLiteral("idle");
            snapshot.detail = QStringLiteral("任务完成");
        } else if (status == QLatin1String("interrupted")) {
            snapshot.state = QStringLiteral("interrupted");
            snapshot.detail = QStringLiteral("任务已中断");
        } else {
            snapshot.state = QStringLiteral("error");
            snapshot.detail = QStringLiteral("任务失败");
        }
        return snapshot;
    }
    if (status != QLatin1String("inProgress") && status != QLatin1String("in_progress")
        && status != QLatin1String("running")) {
        CodexSnapshot snapshot;
        snapshot.threadId = publicThreadId;
        snapshot.turnId = turn;
        snapshot.startedAt = startedAt;
        snapshot.detail = QStringLiteral("暂不支持的任务状态：%1").arg(status.left(40));
        return snapshot;
    }

    QString itemId;
    QString kind;
    QString itemStatus;
    QString toolName;
    QVariant questions;
    bool hasItem = false;
    {
        ScopedReadOnlyDb history(QStringLiteral("codex-history-") + token, historyPath);
        if (!history.isOpen()) {
            return unreadableSnapshot();
        }
        // SQLite 只取出少量元数据，不把对话正文/命令参数/工具输出/推理内容读进内存。
        QSqlQuery query(history.db());
        if (!query.prepare(QStringLiteral(
                "SELECT item_id,"
                "json_extract(item_json,'$.type'),"
                "json_extract(item_json,'$.status'),"
                "json_extract(item_json,'$.tool'),"
                "json_array_length(item_json,'$.questions') "
                "FROM thread_items WHERE thread_id=? AND turn_id=? "
                "ORDER BY rollout_ordinal DESC LIMIT 1"))) {
            return unreadableSnapshot();
        }
        query.addBindValue(session);
        query.addBindValue(turn);
        if (!query.exec()) {
            return unreadableSnapshot();
        }
        if (query.next()) {
            hasItem = true;
            itemId = query.value(0).toString();
            kind = query.value(1).toString();
            itemStatus = query.value(2).toString();
            toolName = query.value(3).toString();
            questions = query.value(4);
        }
    }

    CodexSnapshot snapshot;
    snapshot.threadId = publicThreadId;
    snapshot.turnId = turn;
    snapshot.startedAt = startedAt;

    if (!hasItem) {
        snapshot.state = QStringLiteral("thinking");
        snapshot.detail = QStringLiteral("正在思考");
        return snapshot;
    }

    static const QHash<QString, QString> kindToTool = {
        {QStringLiteral("commandExecution"), QStringLiteral("shell")},
        {QStringLiteral("fileChange"), QStringLiteral("edit")},
        {QStringLiteral("webSearch"), QStringLiteral("web_search")},
        {QStringLiteral("imageGeneration"), QStringLiteral("image_generation")},
        {QStringLiteral("imageView"), QStringLiteral("image_view")},
        {QStringLiteral("mcpToolCall"), QStringLiteral("tool")},
    };
    static const QSet<QString> approvalStates = {
        QStringLiteral("waitingForApproval"), QStringLiteral("pendingApproval"),
        QStringLiteral("waiting_for_approval"), QStringLiteral("waitingForInput"),
    };
    static const QSet<QString> runningStates = {
        QStringLiteral("inProgress"), QStringLiteral("in_progress"),
        QStringLiteral("running"),
    };

    // Python: str(tool_name or tools.get(kind, ''))[:100]
    QString tool = toolName;
    if (tool.isEmpty()) {
        tool = kindToTool.value(kind);
    }
    snapshot.tool = tool.left(100);
    snapshot.itemId = itemId;

    const bool hasQuestions = questions.isValid() && !questions.isNull()
        && ((questions.typeId() == QMetaType::Int || questions.typeId() == QMetaType::LongLong)
                ? questions.toLongLong() > 0
                : !questions.toString().isEmpty());

    if (approvalStates.contains(itemStatus) || hasQuestions) {
        snapshot.state = QStringLiteral("attention");
        snapshot.detail = QStringLiteral("需要你确认或回答");
    } else if (tool.contains(QStringLiteral("request_user_input"))
               && runningStates.contains(itemStatus)) {
        snapshot.state = QStringLiteral("attention");
        snapshot.detail = QStringLiteral("正在等你回答");
    } else if (kindToTool.contains(kind)) {
        snapshot.state = QStringLiteral("working");
        snapshot.detail = QStringLiteral("正在执行任务");
    } else {
        snapshot.state = QStringLiteral("thinking");
        snapshot.detail = QStringLiteral("正在思考或生成回复");
    }
    return snapshot;
}

} // namespace Pet::Services
