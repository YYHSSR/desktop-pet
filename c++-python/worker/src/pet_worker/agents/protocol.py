# -*- coding: utf-8 -*-
"""事件名 → 标准状态词汇的归一化。

对照 ``python/pet/services/agent_link.py`` 的 ``VALID_STATES`` /
``DEFAULT_EVENT_STATE_MAP`` / ``normalize_event_state`` 移植，行为逐行一致。

唯一重要的语义：**不认识的事件返回空串，调用方必须忽略**。
各 Agent 的 transcript 行类型极其繁杂，把未知事件默认当成 ``working`` 会让桌宠
一直在敲键盘——这是旧实现里专门写进注释的教训，不要"优化"掉。

``antigravity_event_state`` / ``antigravity_event_tool`` 那类 transcript 格式解析
留给后续的 Antigravity 适配器，本模块只保留统一协议通道需要的部分。
"""

from __future__ import annotations

#: 标准统一状态词汇。与 protocol/envelope.schema.json 的 state 枚举一致。
VALID_STATES = frozenset({"idle", "thinking", "working", "attention", "sleeping", "error"})

#: 通用事件名到统一状态的默认映射（统一协议 JSONL 的 ``event`` 字段）。
DEFAULT_EVENT_STATE_MAP: dict[str, str] = {
    # 常用生命周期
    "SessionStart": "idle",
    "SessionEnd": "idle",
    "UserPromptSubmit": "thinking",
    "thinking": "thinking",
    "PreInvocation": "thinking",
    "PostInvocation": "working",
    # 工具与执行
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Stop": "attention",
    "StopFailure": "error",
    "SubagentStop": "attention",
    "error": "error",
    "idle": "idle",
}

#: ``notify_state`` 会用到：这些状态值得单独弹「开始干活」气泡。
BUSY_STATES = ("working", "thinking")

#: 需要用户注意的状态（``attention`` / ``error``）。
ALERT_STATES = ("attention", "error")


def normalize_event_state(event_name: str, explicit_state: str = "") -> str:
    """根据事件名或显式 ``state`` 字段规范化为标准状态词汇。

    显式 ``state`` 优先于事件名映射（显式字段是写入方的意图，事件名只是约定）。
    返回空串表示「不认识的事件，忽略」。
    """
    if explicit_state and explicit_state in VALID_STATES:
        return explicit_state
    return DEFAULT_EVENT_STATE_MAP.get(event_name, "")


def extract_tool(event: dict) -> str:
    """从一条事件里取出工具名，取不到返回空串。

    只认顶层 ``tool`` 字段：统一协议里工具名是显式的，不去猜嵌套结构。
    """
    if not isinstance(event, dict):
        return ""
    return str(event.get("tool") or "").strip()


# ----------------------------------------------------------------------
# Antigravity transcript 归一化（对照 core/AgentProtocol.cpp 逐分支）
# ----------------------------------------------------------------------
def _text_of(value: object) -> str:
    """Python 的 str(value or "") 语义：JSON null 与空串等价。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return ""


def _text_of_either(data: dict, primary: str, fallback: str) -> str:
    """data.get(primary) or data.get(fallback) or ""。"""
    first = _text_of(data.get(primary))
    return first if first else _text_of(data.get(fallback))


def _has_tool_calls(data: dict) -> bool:
    """Python: tool_calls and isinstance(tool_calls, (list, tuple)) and len > 0"""
    value = data.get("tool_calls")
    return isinstance(value, (list, tuple)) and len(value) > 0


def antigravity_event_state(data: dict) -> str:
    """把 Antigravity transcript 行归一化为标准状态；不认识返回空串（忽略）。

    分支顺序就是优先级，与 C++ ``antigravityEventState`` 完全一致，
    绝不能为「简洁」调换顺序或合并分支。
    """
    if not isinstance(data, dict):
        return ""
    explicit_state = _text_of(data.get("state"))
    if explicit_state:
        return normalize_event_state("", explicit_state)

    event_type = _text_of_either(data, "type", "event").upper()
    role = _text_of_either(data, "role", "source").upper()
    status = _text_of(data.get("status")).upper()
    tool_calls = _has_tool_calls(data)

    if event_type == "USER_INPUT" or role in ("USER", "USER_EXPLICIT") or event_type in (
        "PREINVOCATION",
        "THINKING",
    ):
        return "thinking"
    if event_type in ("PRETOOLUSE", "POSTTOOLUSE", "POSTINVOCATION"):
        return "working"
    if event_type in ("STOP", "SESSIONEND"):
        return "idle"
    if tool_calls:
        return "working"
    if event_type in ("PLANNER_RESPONSE", "MODEL_RESPONSE", "STEP_START"):
        if status in ("DONE", "FINISHED", "SUCCESS") and not tool_calls:
            return "idle"
        return "working"
    if status in ("DONE", "FINISHED", "SUCCESS"):
        return "idle"
    if status in ("ERROR", "FAILED"):
        return "error"
    return ""


def antigravity_event_tool(data: dict) -> str:
    """从 Antigravity transcript 行提取工具名；取不到返回空串。

    对照 C++ ``antigravityEventTool``：显式 ``tool`` 字段优先，其次
    ``tool_calls[0]`` 的 function.name，再按 fallback 键序逐个尝试。
    """
    if not isinstance(data, dict):
        return ""
    explicit_tool = _text_of(data.get("tool")).strip()
    if explicit_tool:
        return explicit_tool

    tool_calls = data.get("tool_calls")
    if not isinstance(tool_calls, (list, tuple)):
        return ""
    if not tool_calls or not isinstance(tool_calls[0], dict):
        return ""
    first = tool_calls[0]

    function = first.get("function")
    if isinstance(function, dict):
        name = _text_of(function.get("name"))
        if name:
            return name.strip()
    for key in ("name", "tool_name", "toolAction", "toolSummary", "tool"):
        value = _text_of(first.get(key))
        if value:
            return value.strip()
    return ""