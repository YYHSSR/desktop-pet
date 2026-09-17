#pragma once

#include <QJsonObject>
#include <QString>

namespace Pet::Core {

// 1:1 移植自 Python pet/services/agent_link.py 的协议解析层。
//
// 统一状态词汇：idle / thinking / working / attention / sleeping / error。
// 关键设计：无法识别的事件一律返回空串被忽略，绝不默认当作 working——
// 各 Agent 的 transcript 行类型繁杂，默认 working 会导致过度触发。

// Python VALID_STATES
bool isValidAgentState(const QString& state);

// Python normalize_event_state(event_name, explicit_state="")
QString normalizeEventState(const QString& eventName, const QString& explicitState = {});

// Python antigravity_event_state(data)
QString antigravityEventState(const QJsonObject& data);

// Python antigravity_event_tool(data)：取不到返回空串
QString antigravityEventTool(const QJsonObject& data);

} // namespace Pet::Core
