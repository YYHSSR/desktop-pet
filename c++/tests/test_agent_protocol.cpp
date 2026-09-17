// Agent 联动协议层单元测试：
// 1) AgentProtocol 的状态归一化（与 Python agent_link.py 逐条对齐）
// 2) ByteOffsetTailer 的有界增量读取与半行/截断处理
#include "core/AgentProtocol.hpp"
#include "core/ByteOffsetTailer.hpp"

#include <QCoreApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStringList>
#include <QTemporaryDir>

#include <cassert>
#include <iostream>

using Pet::Core::ByteOffsetTailer;

namespace {

QJsonObject parse(const char* json) {
    return QJsonDocument::fromJson(QByteArray(json)).object();
}

void testNormalizeEventState() {
    using Pet::Core::normalizeEventState;

    // DEFAULT_EVENT_STATE_MAP 命中
    assert(normalizeEventState(QStringLiteral("PreToolUse")) == QStringLiteral("working"));
    assert(normalizeEventState(QStringLiteral("PostToolUseFailure")) == QStringLiteral("error"));
    assert(normalizeEventState(QStringLiteral("Stop")) == QStringLiteral("attention"));
    assert(normalizeEventState(QStringLiteral("UserPromptSubmit")) == QStringLiteral("thinking"));
    assert(normalizeEventState(QStringLiteral("SessionEnd")) == QStringLiteral("idle"));

    // 不认识的事件一律返回空串（绝不默认 working）
    assert(normalizeEventState(QStringLiteral("SomethingElse")).isEmpty());
    assert(normalizeEventState(QString()).isEmpty());

    // 显式 state 优先，但必须合法
    assert(normalizeEventState(QString(), QStringLiteral("sleeping")) == QStringLiteral("sleeping"));
    assert(normalizeEventState(QStringLiteral("Stop"), QStringLiteral("error")) == QStringLiteral("error"));
    // 非法显式 state 回退到事件名映射
    assert(normalizeEventState(QStringLiteral("Stop"), QStringLiteral("bogus")) == QStringLiteral("attention"));

    std::cout << "[PASS] normalizeEventState" << std::endl;
}

void testAntigravityProtocol() {
    using Pet::Core::antigravityEventState;
    using Pet::Core::antigravityEventTool;

    // type=USER_INPUT → thinking
    QJsonObject userInput;
    userInput["type"] = "USER_INPUT";
    assert(antigravityEventState(userInput) == QStringLiteral("thinking"));

    // role=USER_EXPLICIT → thinking
    QJsonObject explicitUser;
    explicitUser["role"] = "USER_EXPLICIT";
    assert(antigravityEventState(explicitUser) == QStringLiteral("thinking"));

    // 含 tool_calls 且未完成 → working，并能取到工具名（function.name 优先）
    QJsonObject toolCall;
    QJsonObject function;
    function["name"] = "run_command";
    toolCall["function"] = function;
    QJsonArray toolCalls;
    toolCalls.append(toolCall);

    QJsonObject working;
    working["type"] = "PLANNER_RESPONSE";
    working["tool_calls"] = toolCalls;
    assert(antigravityEventState(working) == QStringLiteral("working"));
    assert(antigravityEventTool(working) == QStringLiteral("run_command"));

    // PLANNER_RESPONSE + status=DONE 且无 tool_calls → idle
    QJsonObject finished;
    finished["type"] = "PLANNER_RESPONSE";
    finished["status"] = "DONE";
    assert(antigravityEventState(finished) == QStringLiteral("idle"));

    // 仅 status=ERROR → error
    QJsonObject errored;
    errored["status"] = "ERROR";
    assert(antigravityEventState(errored) == QStringLiteral("error"));

    // type=STOP → idle；PreToolUse → working
    QJsonObject stop;
    stop["type"] = "STOP";
    assert(antigravityEventState(stop) == QStringLiteral("idle"));
    QJsonObject preTool;
    preTool["type"] = "PreToolUse";
    assert(antigravityEventState(preTool) == QStringLiteral("working"));

    // 显式 tool 字段优先级最高
    QJsonObject explicitTool;
    explicitTool["tool"] = "view_file";
    explicitTool["tool_calls"] = toolCalls;
    assert(antigravityEventTool(explicitTool) == QStringLiteral("view_file"));

    // tool_calls 回退键
    QJsonObject fallbackTool;
    QJsonObject firstCall;
    firstCall["tool_name"] = "grep_search";
    QJsonArray fallbackCalls;
    fallbackCalls.append(firstCall);
    fallbackTool["tool_calls"] = fallbackCalls;
    assert(antigravityEventTool(fallbackTool) == QStringLiteral("grep_search"));

    // 无法判定时返回空串
    QJsonObject blank;
    assert(antigravityEventState(blank).isEmpty());
    assert(antigravityEventTool(blank).isEmpty());

    std::cout << "[PASS] antigravity protocol" << std::endl;
}

void testByteOffsetTailer() {
    QTemporaryDir directory;
    assert(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("events.jsonl"));

    // 预置历史内容
    {
        QFile file(path);
        assert(file.open(QIODevice::WriteOnly));
        file.write("{\"event\":\"history\"}\n");
    }

    ByteOffsetTailer tailer(path);
    // 首次读取执行 backfill 防护：跳过历史内容
    assert(tailer.readNewLines().isEmpty());

    // 追加完整行 → 读到一行
    {
        QFile file(path);
        assert(file.open(QIODevice::Append));
        file.write("{\"event\":\"Stop\"}\n");
    }
    QStringList lines = tailer.readNewLines();
    assert(lines.size() == 1);
    assert(lines.first().contains(QStringLiteral("Stop")));

    // 无新增时不重复返回
    assert(tailer.readNewLines().isEmpty());

    // 半行缓冲：末尾无换行的内容不得当作整行解析
    {
        QFile file(path);
        assert(file.open(QIODevice::Append));
        file.write("{\"event\":\"Pre");
    }
    assert(tailer.readNewLines().isEmpty());

    // 补齐换行后整行才交付
    {
        QFile file(path);
        assert(file.open(QIODevice::Append));
        file.write("ToolUse\"}\n");
    }
    lines = tailer.readNewLines();
    assert(lines.size() == 1);
    assert(lines.first().contains(QStringLiteral("PreToolUse")));

    // 截断轮转：安全重置到文件头并交付新内容
    {
        QFile file(path);
        assert(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
        file.write("{\"event\":\"error\"}\n");
    }
    lines = tailer.readNewLines();
    assert(lines.size() == 1);
    assert(lines.first().contains(QStringLiteral("error")));

    // BOM 容错：PowerShell Add-Content -Encoding UTF8 会在首行写入 BOM
    const QString bomPath = directory.filePath(QStringLiteral("bom.jsonl"));
    ByteOffsetTailer bomTailer(bomPath);
    assert(bomTailer.readNewLines().isEmpty());
    {
        QFile file(bomPath);
        assert(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
        file.write("\xEF\xBB\xBF{\"event\":\"Stop\"}\n");
    }
    lines = bomTailer.readNewLines();
    // 首次调用仍是 backfill（跳过已存在内容），需要再来一次追加
    assert(lines.isEmpty());
    {
        QFile file(bomPath);
        assert(file.open(QIODevice::Append));
        file.write("\xEF\xBB\xBF{\"event\":\"thinking\"}\n");
    }
    lines = bomTailer.readNewLines();
    assert(lines.size() == 1);
    assert(lines.first().startsWith(QLatin1Char('{')));

    std::cout << "[PASS] ByteOffsetTailer" << std::endl;
}

} // namespace

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    std::cout << "Running Agent link protocol tests..." << std::endl;

    testNormalizeEventState();
    testAntigravityProtocol();
    testByteOffsetTailer();

    std::cout << "All agent protocol tests passed." << std::endl;
    return 0;
}
