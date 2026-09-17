#include "AgentProtocol.hpp"

#include <QHash>
#include <QJsonArray>
#include <QJsonValue>
#include <QSet>
#include <cmath>

namespace Pet::Core {
namespace {

// Python DEFAULT_EVENT_STATE_MAP
const QHash<QString, QString>& defaultEventStateMap() {
    static const QHash<QString, QString> map = {
        {QStringLiteral("SessionStart"), QStringLiteral("idle")},
        {QStringLiteral("SessionEnd"), QStringLiteral("idle")},
        {QStringLiteral("UserPromptSubmit"), QStringLiteral("thinking")},
        {QStringLiteral("thinking"), QStringLiteral("thinking")},
        {QStringLiteral("PreInvocation"), QStringLiteral("thinking")},
        {QStringLiteral("PostInvocation"), QStringLiteral("working")},
        {QStringLiteral("PreToolUse"), QStringLiteral("working")},
        {QStringLiteral("PostToolUse"), QStringLiteral("working")},
        {QStringLiteral("PostToolUseFailure"), QStringLiteral("error")},
        {QStringLiteral("Stop"), QStringLiteral("attention")},
        {QStringLiteral("StopFailure"), QStringLiteral("error")},
        {QStringLiteral("SubagentStop"), QStringLiteral("attention")},
        {QStringLiteral("error"), QStringLiteral("error")},
        {QStringLiteral("idle"), QStringLiteral("idle")},
    };
    return map;
}

// Python 的 str(value or "") 语义：JSON null 与空串等价。
QString textOf(const QJsonValue& value) {
    if (value.isNull() || value.isUndefined()) {
        return QString();
    }
    if (value.isString()) {
        return value.toString();
    }
    if (value.isBool()) {
        return value.toBool() ? QStringLiteral("True") : QStringLiteral("False");
    }
    if (value.isDouble()) {
        const double number = value.toDouble();
        if (qFuzzyCompare(number, std::floor(number))) {
            return QString::number(static_cast<qint64>(number));
        }
        return QString::number(number);
    }
    return QString();
}

// data.get(primary) or data.get(fallback) or ""
QString textOfEither(const QJsonObject& data, const QString& primary, const QString& fallback) {
    const QString first = textOf(data.value(primary));
    return first.isEmpty() ? textOf(data.value(fallback)) : first;
}

// Python: tool_calls and isinstance(tool_calls, (list, tuple)) and len > 0
bool hasToolCalls(const QJsonObject& data) {
    const QJsonValue value = data.value(QStringLiteral("tool_calls"));
    return value.isArray() && !value.toArray().isEmpty();
}

bool inList(const QString& value, std::initializer_list<const char*> options) {
    for (const char* option : options) {
        if (value == QLatin1String(option)) {
            return true;
        }
    }
    return false;
}

} // namespace

bool isValidAgentState(const QString& state) {
    static const QSet<QString> valid = {
        QStringLiteral("idle"), QStringLiteral("thinking"), QStringLiteral("working"),
        QStringLiteral("attention"), QStringLiteral("sleeping"), QStringLiteral("error"),
    };
    return valid.contains(state);
}

QString normalizeEventState(const QString& eventName, const QString& explicitState) {
    if (!explicitState.isEmpty() && isValidAgentState(explicitState)) {
        return explicitState;
    }
    return defaultEventStateMap().value(eventName);
}

QString antigravityEventState(const QJsonObject& data) {
    const QString explicitState = textOf(data.value(QStringLiteral("state")));
    if (!explicitState.isEmpty()) {
        return normalizeEventState(QString(), explicitState);
    }

    const QString eventType = textOfEither(data, QStringLiteral("type"), QStringLiteral("event")).toUpper();
    const QString role = textOfEither(data, QStringLiteral("role"), QStringLiteral("source")).toUpper();
    const QString status = textOf(data.value(QStringLiteral("status"))).toUpper();
    const bool toolCalls = hasToolCalls(data);

    if (eventType == QLatin1String("USER_INPUT")
        || inList(role, {"USER", "USER_EXPLICIT"})
        || inList(eventType, {"PREINVOCATION", "THINKING"})) {
        return QStringLiteral("thinking");
    }
    if (inList(eventType, {"PRETOOLUSE", "POSTTOOLUSE", "POSTINVOCATION"})) {
        return QStringLiteral("working");
    }
    if (inList(eventType, {"STOP", "SESSIONEND"})) {
        return QStringLiteral("idle");
    }
    if (toolCalls) {
        return QStringLiteral("working");
    }
    if (inList(eventType, {"PLANNER_RESPONSE", "MODEL_RESPONSE", "STEP_START"})) {
        if (inList(status, {"DONE", "FINISHED", "SUCCESS"}) && !toolCalls) {
            return QStringLiteral("idle");
        }
        return QStringLiteral("working");
    }
    if (inList(status, {"DONE", "FINISHED", "SUCCESS"})) {
        return QStringLiteral("idle");
    }
    if (inList(status, {"ERROR", "FAILED"})) {
        return QStringLiteral("error");
    }
    return QString();
}

QString antigravityEventTool(const QJsonObject& data) {
    const QString explicitTool = textOf(data.value(QStringLiteral("tool"))).trimmed();
    if (!explicitTool.isEmpty()) {
        return explicitTool;
    }

    const QJsonValue toolCallsValue = data.value(QStringLiteral("tool_calls"));
    if (!toolCallsValue.isArray()) {
        return QString();
    }
    const QJsonArray toolCalls = toolCallsValue.toArray();
    if (toolCalls.isEmpty() || !toolCalls.first().isObject()) {
        return QString();
    }
    const QJsonObject first = toolCalls.first().toObject();

    const QJsonValue function = first.value(QStringLiteral("function"));
    if (function.isObject()) {
        const QString name = textOf(function.toObject().value(QStringLiteral("name")));
        if (!name.isEmpty()) {
            return name.trimmed();
        }
    }
    static const char* const fallbackKeys[] = {"name", "tool_name", "toolAction", "toolSummary", "tool"};
    for (const char* key : fallbackKeys) {
        const QString value = textOf(first.value(QLatin1String(key)));
        if (!value.isEmpty()) {
            return value.trimmed();
        }
    }
    return QString();
}

} // namespace Pet::Core
