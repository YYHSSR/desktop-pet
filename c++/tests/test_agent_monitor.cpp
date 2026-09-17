// Agent 联动集成测试：
// 1) CustomAgentMonitor 的增量事件流（文件 → tailer → 协议 → 信号）
// 2) AntigravityMonitor 的 hooks 注入/卸载（写入隔离的临时 hooks.json）
// 3) CodexStatusReader 的只读 SQLite 快照解析
#include "services/agent/AntigravityMonitor.hpp"
#include "services/agent/CodexStatusReader.hpp"
#include "services/agent/CustomAgentMonitor.hpp"

#include <QCoreApplication>
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSqlDatabase>
#include <QSqlQuery>
#include <QTemporaryDir>
#include <QTimer>
#include <QVariant>

#include <cassert>
#include <iostream>

namespace {

void waitMs(int milliseconds) {
    QEventLoop loop;
    QTimer::singleShot(milliseconds, &loop, &QEventLoop::quit);
    loop.exec();
}

void appendLine(const QString& path, const QByteArray& line) {
    QFile file(path);
    assert(file.open(QIODevice::Append));
    file.write(line);
    file.write("\n");
}

void testCustomAgentMonitorFlow() {
    QTemporaryDir directory;
    assert(directory.isValid());
    const QString eventsPath = directory.filePath(QStringLiteral("gemini.jsonl"));

    // 预置历史事件：首次轮询执行 backfill 防护，不应重放
    appendLine(eventsPath, R"({"event":"Stop"})");

    Pet::Services::CustomAgentMonitor monitor(
        QStringLiteral("gemini"), directory.path(), eventsPath);

    QStringList states;
    QList<QPair<QString, QString>> activities;
    QObject::connect(&monitor, &Pet::Services::BaseAgentMonitor::stateChanged,
                     [&states](const QString&, const QString& state) { states.append(state); });
    QObject::connect(&monitor, &Pet::Services::BaseAgentMonitor::activity,
                     [&activities](const QString& agent, const QString& tool) {
                         activities.append({agent, tool});
                     });

    monitor.start();
    // 第一次轮询：backfill，跳过历史内容
    waitMs(1800);
    assert(states.isEmpty());
    assert(activities.isEmpty());

    // 新事件：工具名随事件一起上报
    appendLine(eventsPath, R"({"event":"PreToolUse","tool":"read"})");
    // 不认识的事件类型必须被忽略（不得误报 working）
    appendLine(eventsPath, R"({"event":"WhateverUnknown"})");
    // 显式 state 通道
    appendLine(eventsPath, R"({"state":"attention"})");
    waitMs(1800);

    assert(states.contains(QStringLiteral("working")));
    assert(states.contains(QStringLiteral("attention")));
    // 未知事件未产生状态
    assert(states.size() == 3);
    assert(activities.size() == 1);
    assert(activities.first().first == QStringLiteral("gemini"));
    assert(activities.first().second == QStringLiteral("read"));

    monitor.stop();
    assert(!monitor.isRunning());

    std::cout << "[PASS] CustomAgentMonitor incremental flow" << std::endl;
}

void testAntigravityHooks() {
    QTemporaryDir directory;
    assert(directory.isValid());
    const QString eventsFile =
        directory.filePath(QStringLiteral("agent-events/antigravity.jsonl"));
    const QString hooksFile = directory.filePath(QStringLiteral("hooks.json"));

    assert(Pet::Services::AntigravityMonitor::installHooks(eventsFile, hooksFile));

    // 事件写入脚本应落在事件文件同目录
    const QString script =
        directory.filePath(QStringLiteral("agent-events/antigravity_event_hook.ps1"));
    assert(QFile::exists(script));

    QFile hooks(hooksFile);
    assert(hooks.open(QIODevice::ReadOnly));
    const QJsonObject data = QJsonDocument::fromJson(hooks.readAll()).object();
    hooks.close();

    assert(data.contains(QStringLiteral("desktop-pet-hook")));
    const QJsonObject hookBlock = data.value(QStringLiteral("desktop-pet-hook")).toObject();
    assert(hookBlock.value(QStringLiteral("x-desktop-pet")).toBool());
    for (const QString& event : {QStringLiteral("PreInvocation"), QStringLiteral("PreToolUse"),
                                 QStringLiteral("PostToolUse"), QStringLiteral("Stop")}) {
        assert(hookBlock.contains(event));
    }
    // PreToolUse 走 matcher 包装形式
    const QJsonObject preTool =
        hookBlock.value(QStringLiteral("PreToolUse")).toArray().first().toObject();
    assert(preTool.value(QStringLiteral("matcher")).toString() == QStringLiteral("*"));
    assert(preTool.value(QStringLiteral("hooks")).toArray().size() == 1);
    // 命令指向生成的脚本并带事件名
    const QString command = hookBlock.value(QStringLiteral("PreInvocation"))
                                .toArray().first().toObject()
                                .value(QStringLiteral("command")).toString();
    assert(command.contains(script));
    assert(command.contains(QStringLiteral("PreInvocation")));
    assert(command.startsWith(QStringLiteral("powershell -NoProfile -ExecutionPolicy Bypass")));

    // 卸载只移除自己注入的键，不动用户自有配置
    {
        QFile file(hooksFile);
        assert(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
        QJsonObject merged = data;
        merged[QStringLiteral("my-own-hook")] = true;
        file.write(QJsonDocument(merged).toJson());
    }
    assert(Pet::Services::AntigravityMonitor::uninstallHooks(hooksFile));

    QFile after(hooksFile);
    assert(after.open(QIODevice::ReadOnly));
    const QJsonObject cleaned = QJsonDocument::fromJson(after.readAll()).object();
    after.close();
    assert(!cleaned.contains(QStringLiteral("desktop-pet-hook")));
    assert(cleaned.value(QStringLiteral("my-own-hook")).toBool());

    std::cout << "[PASS] Antigravity hooks install/uninstall" << std::endl;
}

bool createSqlite(const QString& path, const QStringList& statements) {
    const QString connection = QStringLiteral("test-%1").arg(path);
    {
        QSqlDatabase db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connection);
        db.setDatabaseName(path);
        if (!db.open()) {
            return false;
        }
        QSqlQuery query(db);
        for (const QString& statement : statements) {
            if (!query.exec(statement)) {
                db.close();
                QSqlDatabase::removeDatabase(connection);
                return false;
            }
        }
        db.close();
    }
    QSqlDatabase::removeDatabase(connection);
    return true;
}

void testCodexStatusReader() {
    QTemporaryDir directory;
    assert(directory.isValid());
    const QString home = directory.path();

    // state 库：一条未归档的主任务，rollout 名为 rollout_<runId>.jsonl
    assert(createSqlite(
        QDir(home).filePath(QStringLiteral("state_1.sqlite")),
        {
            QStringLiteral("CREATE TABLE threads (id TEXT, rollout_path TEXT, archived INTEGER, "
                           "updated_at INTEGER)"),
            QStringLiteral("INSERT INTO threads VALUES ('t1','/x/rollout_abc.jsonl',0,10)"),
        }));

    // thread_history 库：进行中的回合 + 最新 item 为命令执行
    assert(createSqlite(
        QDir(home).filePath(QStringLiteral("thread_history_1.sqlite")),
        {
            QStringLiteral("CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, "
                           "started_at INTEGER)"),
            QStringLiteral("INSERT INTO thread_turns VALUES ('abc','turn1','inProgress',100)"),
            QStringLiteral("CREATE TABLE thread_items (item_id TEXT, thread_id TEXT, "
                           "turn_id TEXT, rollout_ordinal INTEGER, item_json TEXT)"),
            QStringLiteral("INSERT INTO thread_items VALUES "
                           "('i1','abc','turn1',1,'{\"type\":\"commandExecution\","
                           "\"status\":\"inProgress\"}')"),
        }));

    Pet::Services::CodexStatusReader reader(home);
    const Pet::Services::CodexSnapshot snapshot = reader.read();

    // rollout 名映射：runId 回填为公开 thread id
    assert(snapshot.threadId == QStringLiteral("t1"));
    assert(snapshot.turnId == QStringLiteral("turn1"));
    // commandExecution → shell → working
    assert(snapshot.state == QStringLiteral("working"));
    assert(snapshot.tool == QStringLiteral("shell"));
    assert(snapshot.itemId == QStringLiteral("i1"));
    assert(snapshot.startedAt == 100);

    std::cout << "[PASS] CodexStatusReader read-only snapshot" << std::endl;
}

} // namespace

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    std::cout << "Running Agent monitor integration tests..." << std::endl;

    testCustomAgentMonitorFlow();
    testAntigravityHooks();
    testCodexStatusReader();

    std::cout << "All agent monitor tests passed." << std::endl;
    return 0;
}
