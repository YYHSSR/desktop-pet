# -*- coding: utf-8 -*-
"""协议契约的单一来源。

与 ``c++-python/protocol/envelope.schema.json`` 一一对应，并消费同一份
``c++-python/protocol/fixtures/`` 夹具。任何字段、枚举、上限的修改都必须先改
schema 与夹具，再改本文件——不允许两端各自「就地修好」。

本模块不做 I/O、不依赖任何第三方库。
"""

from __future__ import annotations

import re
from typing import Any

# ----------------------------------------------------------------------
# 版本与上限
# ----------------------------------------------------------------------
PROTOCOL_VERSION = 1

MAX_MESSAGE_BYTES = 65536
MAX_OUTBOUND_QUEUE_MESSAGES = 256
MAX_OUTBOUND_QUEUE_BYTES = 1048576
HANDSHAKE_TIMEOUT_MS = 5000
HEARTBEAT_INTERVAL_MS = 2000
HEARTBEAT_MISS_THRESHOLD = 3
RESTART_BACKOFF_MS = (1000, 2000, 4000)
RESTART_WINDOW_MS = 60000
MAX_RESTARTS_PER_WINDOW = 3
SHUTDOWN_GRACE_MS = 2000
DEFAULT_PROPOSAL_TTL_MS = 2000

DEFAULT_PET_ID = "main"

# ----------------------------------------------------------------------
# 信封
# ----------------------------------------------------------------------
KIND_REQUEST = "request"
KIND_RESPONSE = "response"
KIND_EVENT = "event"
KINDS = (KIND_REQUEST, KIND_RESPONSE, KIND_EVENT)

ENVELOPE_KEYS = (
    "protocol_version",
    "session_id",
    "seq",
    "kind",
    "type",
    "pet_id",
    "request_id",
    "payload",
)

# ----------------------------------------------------------------------
# 消息类型
# ----------------------------------------------------------------------
MSG_RUNTIME_HELLO = "runtime.hello"
MSG_RUNTIME_READY = "runtime.ready"
MSG_CONFIG_SNAPSHOT = "config.snapshot"
MSG_PET_SNAPSHOT = "pet.snapshot"
MSG_PET_EVENT = "pet.event"
MSG_AGENT_STATE_CHANGED = "agent.state_changed"
MSG_BEHAVIOR_PROPOSE = "behavior.propose"
MSG_BEHAVIOR_RESULT = "behavior.result"
MSG_CONFIG_PATCH_REQUEST = "config.patch_request"
MSG_CONFIG_PATCH_RESULT = "config.patch_result"
MSG_RUNTIME_PING = "runtime.ping"
MSG_RUNTIME_PONG = "runtime.pong"
MSG_RUNTIME_SHUTDOWN = "runtime.shutdown"
MSG_UNSUPPORTED_MESSAGE = "runtime.unsupported_message"

#: 宿主 → Worker 的消息（Worker 必须能处理；不认识的一律 runtime.unsupported_message 或不触发动作）
INBOUND_TYPES = frozenset(
    {
        MSG_RUNTIME_HELLO,
        MSG_CONFIG_SNAPSHOT,
        MSG_PET_SNAPSHOT,
        MSG_PET_EVENT,
        MSG_BEHAVIOR_RESULT,
        MSG_CONFIG_PATCH_RESULT,
        MSG_RUNTIME_PING,
        MSG_RUNTIME_PONG,
        MSG_RUNTIME_SHUTDOWN,
    }
)

#: Worker → 宿主的消息
OUTBOUND_TYPES = frozenset(
    {
        MSG_RUNTIME_READY,
        MSG_AGENT_STATE_CHANGED,
        MSG_BEHAVIOR_PROPOSE,
        MSG_CONFIG_PATCH_REQUEST,
        MSG_RUNTIME_PING,
        MSG_RUNTIME_PONG,
        MSG_UNSUPPORTED_MESSAGE,
    }
)

#: 全部已定义类型。不在其中的类型记入 unsupported，绝不触发默认动作。
KNOWN_TYPES = INBOUND_TYPES | OUTBOUND_TYPES

# ----------------------------------------------------------------------
# 业务词汇（复用现有 agent_link 语义，不引入第二套）
# ----------------------------------------------------------------------
AGENT_STATES = ("idle", "thinking", "working", "attention", "sleeping", "error")
VALID_STATES = frozenset(AGENT_STATES)
BUSY_STATES = ("working", "thinking")

AGENT_BACKENDS = ("native", "python")

PET_EVENT_NAMES = (
    "click",
    "drag_start",
    "drag_end",
    "action_finished",
    "hidden",
    "shown",
    "character_changed",
)

BEHAVIOR_STATUSES = ("accepted", "rejected", "expired")
BEHAVIOR_REASONS = (
    "interaction_locked",
    "generation_mismatch",
    "revision_mismatch",
    "unknown_action",
    "bubble_busy",
    "channel_unhealthy",
    "not_ready",
)

PATCH_STATUSES = ("applied", "rejected")
UNSUPPORTED_REASONS = (
    "unknown_type",
    "invalid_envelope",
    "unsupported_version",
    "payload_too_large",
    "unsupported_field",
)

#: 返回给宿主的能力集合（宿主按 required_capabilities 判定是否降级）
CAPABILITIES = (
    "state",           # agent.state_changed
    "behavior",        # behavior.propose
    "custom_agent",    # 只读监听统一协议 JSONL
    "config_patch",    # config.patch_request（白名单）
)

#: 系统保留动作：不是角色包里的真实动作，由宿主仲裁器直接解释。
#: ``system/idle`` = 回到待机；``system/bubble`` = 只冒泡，不改变当前动作。
#: 分成两个是为了让「气泡」与「动作」能独立被拒绝——
#: 拖拽中动作被拒绝很正常，但"Agent 干完活了"这条通知不该跟着一起丢。
SYSTEM_ACTION_IDLE = "system/idle"
SYSTEM_ACTION_BUBBLE = "system/bubble"
SYSTEM_ACTIONS = (SYSTEM_ACTION_IDLE, SYSTEM_ACTION_BUBBLE)

# ----------------------------------------------------------------------
# 上限
# ----------------------------------------------------------------------
MAX_SESSION_ID_LEN = 64
MIN_SESSION_ID_LEN = 8
MAX_REQUEST_ID_LEN = 64
MAX_PET_ID_LEN = 64
MAX_ACTION_ID_LEN = 128
MAX_ACTIONS = 512
MAX_BUBBLE_TEXT_LEN = 256
MIN_BUBBLE_DURATION_MS = 500
MAX_BUBBLE_DURATION_MS = 20000
DEFAULT_BUBBLE_DURATION_MS = 4500
MIN_TTL_MS = 1
MAX_TTL_MS = 30000
MAX_SHUTDOWN_TIMEOUT_MS = 2000

# ----------------------------------------------------------------------
# 分帧错误码（与 fixtures/framing_cases.json 的 error_kinds 一致）
# ----------------------------------------------------------------------
ERR_INVALID_JSON = "invalid_json"
ERR_INVALID_UTF8 = "invalid_utf8"
ERR_INVALID_ENVELOPE = "invalid_envelope"
ERR_PAYLOAD_TOO_LARGE = "payload_too_large"
ERR_UNSUPPORTED_VERSION = "unsupported_version"
ERR_TRUNCATED_ON_EOF = "truncated_on_eof"

ERROR_KINDS = (
    ERR_INVALID_JSON,
    ERR_INVALID_UTF8,
    ERR_INVALID_ENVELOPE,
    ERR_PAYLOAD_TOO_LARGE,
    ERR_UNSUPPORTED_VERSION,
    ERR_TRUNCATED_ON_EOF,
)

#: 不算「错误计数」但需要单独上报的两类
ERR_UNKNOWN_TYPE = "unknown_type"
ERR_DUPLICATE_SEQ = "duplicate_seq"

# ----------------------------------------------------------------------
# 正则
# ----------------------------------------------------------------------
_RE_SESSION_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_RE_PET_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_RE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_RE_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_RE_AGENT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ProtocolError(Exception):
    """协议层错误。``kind`` 取 ``ERROR_KINDS`` 之一。"""

    __slots__ = ("kind", "detail")

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class UnknownMessageType(ProtocolError):
    """信封合法但 ``type`` 未定义。不记入错误计数，只记入 unsupported 列表。"""

    __slots__ = ("message_type",)

    def __init__(self, message_type: str) -> None:
        super().__init__(ERR_UNKNOWN_TYPE, message_type)
        self.message_type = message_type


# ----------------------------------------------------------------------
# 字段类型判定
# ----------------------------------------------------------------------
def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_bubble(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    if set(value) - {"text", "important", "duration_ms"}:
        return False
    text = value.get("text")
    if not isinstance(text, str) or not (1 <= len(text) <= MAX_BUBBLE_TEXT_LEN):
        return False
    if "important" in value and not isinstance(value["important"], bool):
        return False
    if "duration_ms" in value:
        duration = value["duration_ms"]
        if not _is_int(duration) or not (MIN_BUBBLE_DURATION_MS <= duration <= MAX_BUBBLE_DURATION_MS):
            return False
    return True


def _check_agents_map(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if not isinstance(key, str) or not _RE_AGENT_KEY.match(key):
            return False
        if not isinstance(item, dict):
            return False
        if set(item) - {"enabled", "kind", "display_name", "events_path", "busy_agents", "thinking_text"}:
            return False
        if not isinstance(item.get("enabled"), bool):
            return False
        if item.get("kind") not in {"custom", "antigravity", "chatgpt"}:
            return False
        for text_key in ("display_name", "events_path", "thinking_text"):
            if text_key in item and not isinstance(item[text_key], str):
                return False
        if "busy_agents" in item and not _is_str_list(item["busy_agents"]):
            return False
    return True


def _check_action_id(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_ACTION_ID_LEN


#: 策略参数：全部可省，省了就用 Worker 默认值（与旧版 agent_link 行为一致）。
POLICY_BOOL_FIELDS = ("notify_state", "notify_done", "notify_activity")
POLICY_MS_FIELDS = {
    "min_interval_ms": 60000,
    "activity_interval_ms": 600000,
    "activity_global_min_ms": 600000,
    "activity_same_label_ms": 600000,
    "done_confirm_ms": 10000,
    "done_cooldown_ms": 600000,
}


def _check_policy(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) - set(POLICY_BOOL_FIELDS) - set(POLICY_MS_FIELDS):
        return False
    for key in POLICY_BOOL_FIELDS:
        if key in value and not isinstance(value[key], bool):
            return False
    for key, maximum in POLICY_MS_FIELDS.items():
        if key not in value:
            continue
        item = value[key]
        if not _is_int(item) or not (0 <= item <= maximum):
            return False
    return True


def _check_action_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= MAX_ACTIONS
        and all(_check_action_id(item) for item in value)
    )


def check_field(field_type: str, value: Any) -> bool:
    """按 schema 的字段类型名校验单个字段值。"""
    if field_type == "any":
        return True
    if field_type == "str":
        return isinstance(value, str)
    if field_type == "str_or_null":
        return value is None or isinstance(value, str)
    if field_type == "bool":
        return isinstance(value, bool)
    if field_type == "int":
        return _is_int(value)
    if field_type == "int_min0":
        return _is_int(value) and value >= 0
    if field_type == "int_pos":
        return _is_int(value) and value >= 1
    if field_type == "int_ttl":
        return _is_int(value) and MIN_TTL_MS <= value <= MAX_TTL_MS
    if field_type == "int_shutdown_timeout":
        return _is_int(value) and 0 <= value <= MAX_SHUTDOWN_TIMEOUT_MS
    if field_type == "str_list":
        return _is_str_list(value)
    if field_type == "obj":
        return isinstance(value, dict)
    if field_type == "agent_key":
        return isinstance(value, str) and bool(_RE_AGENT_KEY.match(value))
    if field_type == "agent_state":
        return isinstance(value, str) and value in VALID_STATES
    if field_type == "action_id":
        return _check_action_id(value)
    if field_type == "action_list":
        return _check_action_list(value)
    if field_type == "bubble":
        return _check_bubble(value)
    if field_type == "agents_map":
        return _check_agents_map(value)
    if field_type == "policy_cfg":
        return _check_policy(value)
    if field_type.startswith("enum:"):
        return isinstance(value, str) and value in tuple(field_type[5:].split("|"))
    raise ValueError(f"未知字段类型: {field_type}")  # pragma: no cover - 仅开发期触发


# ----------------------------------------------------------------------
# payload 规格
# ----------------------------------------------------------------------
def _enum(values: tuple[str, ...]) -> str:
    return "enum:" + "|".join(values)


#: ``kind`` 为 None 表示三种 kind 都允许（ping/pong 双向共用）。
PAYLOAD_SPECS: dict[str, dict[str, Any]] = {
    MSG_RUNTIME_HELLO: {
        "kind": KIND_REQUEST,
        "required": {"host_version": "str", "required_capabilities": "str_list"},
        "optional": {},
    },
    MSG_RUNTIME_READY: {
        "kind": KIND_RESPONSE,
        "required": {"worker_version": "str", "capabilities": "str_list"},
        "optional": {"pid": "int_pos"},
    },
    MSG_CONFIG_SNAPSHOT: {
        "kind": KIND_EVENT,
        "required": {"revision": "int_min0", "agent_backend": _enum(AGENT_BACKENDS), "agents": "agents_map"},
        "optional": {"policy": "policy_cfg"},
    },
    MSG_PET_SNAPSHOT: {
        "kind": KIND_EVENT,
        "required": {
            "generation": "int_min0",
            "character": "str",
            "visible": "bool",
            "interaction_locked": "bool",
            "actions": "action_list",
        },
        "optional": {},
    },
    MSG_PET_EVENT: {
        "kind": KIND_EVENT,
        "required": {"name": _enum(PET_EVENT_NAMES), "generation": "int_min0"},
        "optional": {"action_id": "action_id"},
    },
    MSG_AGENT_STATE_CHANGED: {
        "kind": KIND_EVENT,
        "required": {
            "agent": "agent_key",
            "state": "agent_state",
            "generation": "int_min0",
            "config_revision": "int_min0",
        },
        "optional": {"tool": "str"},
    },
    MSG_BEHAVIOR_PROPOSE: {
        "kind": KIND_REQUEST,
        "required": {"action_id": "action_id", "generation": "int_min0", "config_revision": "int_min0"},
        "optional": {"ttl_ms": "int_ttl", "agent": "agent_key", "bubble": "bubble"},
    },
    MSG_BEHAVIOR_RESULT: {
        "kind": KIND_RESPONSE,
        "required": {"status": _enum(BEHAVIOR_STATUSES)},
        "optional": {"reason": "str"},
    },
    MSG_CONFIG_PATCH_REQUEST: {
        "kind": KIND_REQUEST,
        "required": {"expected_revision": "int_min0", "patch": "obj"},
        "optional": {},
    },
    MSG_CONFIG_PATCH_RESULT: {
        "kind": KIND_RESPONSE,
        "required": {"status": _enum(PATCH_STATUSES)},
        "optional": {"reason": "str", "revision": "int_min0"},
    },
    MSG_RUNTIME_PING: {"kind": None, "required": {}, "optional": {}},
    MSG_RUNTIME_PONG: {"kind": None, "required": {}, "optional": {}},
    MSG_RUNTIME_SHUTDOWN: {
        # event：进程退出即回执，不产生 behavior/patch 那样的结果消息。
        "kind": KIND_EVENT,
        "required": {},
        "optional": {"timeout_ms": "int_shutdown_timeout"},
    },
    MSG_UNSUPPORTED_MESSAGE: {
        "kind": KIND_RESPONSE,
        "required": {"reason": _enum(UNSUPPORTED_REASONS)},
        "optional": {"detail": "str"},
    },
}


def _validate_payload(message_type: str, payload: Any) -> None:
    spec = PAYLOAD_SPECS[message_type]
    if not isinstance(payload, dict):
        raise ProtocolError(ERR_INVALID_ENVELOPE, "payload 必须是对象")
    required: dict[str, str] = spec["required"]
    optional: dict[str, str] = spec["optional"]

    missing = [key for key in required if key not in payload]
    if missing:
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"payload 缺少字段 {','.join(sorted(missing))}")

    extra = set(payload) - set(required) - set(optional)
    if extra:
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"payload 含未定义字段 {','.join(sorted(extra))}")

    for key, field_type in required.items():
        if not check_field(field_type, payload[key]):
            raise ProtocolError(ERR_INVALID_ENVELOPE, f"payload.{key} 类型/取值非法")
    for key, field_type in optional.items():
        if key in payload and not check_field(field_type, payload[key]):
            raise ProtocolError(ERR_INVALID_ENVELOPE, f"payload.{key} 类型/取值非法")


# ----------------------------------------------------------------------
# 信封校验
# ----------------------------------------------------------------------
def validate_envelope(message: Any) -> dict:
    """校验并返回原消息。

    - 结构 / 字段 / 枚举非法 → ``ProtocolError(kind=invalid_envelope)``
    - ``protocol_version`` 不匹配 → ``ProtocolError(kind=unsupported_version)``
    - ``type`` 未定义 → ``UnknownMessageType``

    调用方按异常类型决定是记错误计数、记 unsupported，还是投递。
    """
    if not isinstance(message, dict):
        raise ProtocolError(ERR_INVALID_ENVELOPE, "顶层必须是对象")

    extra = set(message) - set(ENVELOPE_KEYS)
    if extra:
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"含未定义字段 {','.join(sorted(extra))}")
    missing = [key for key in ENVELOPE_KEYS if key not in message]
    if missing:
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"缺少字段 {','.join(sorted(missing))}")

    version = message["protocol_version"]
    if not _is_int(version):
        raise ProtocolError(ERR_INVALID_ENVELOPE, "protocol_version 必须是整数")
    if version != PROTOCOL_VERSION:
        # 主版本不兼容：不投递、不中止会话，宿主据此降级为 native。
        raise ProtocolError(ERR_UNSUPPORTED_VERSION, f"protocol_version={version}")

    kind = message["kind"]
    if kind not in KINDS:
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"kind={kind!r}")

    message_type = message["type"]
    if not isinstance(message_type, str) or not _RE_TYPE.match(message_type):
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"type={message_type!r}")

    session_id = message["session_id"]
    if (
        not isinstance(session_id, str)
        or not (MIN_SESSION_ID_LEN <= len(session_id) <= MAX_SESSION_ID_LEN)
        or not _RE_SESSION_ID.match(session_id)
    ):
        raise ProtocolError(ERR_INVALID_ENVELOPE, "session_id 非法")

    pet_id = message["pet_id"]
    if not isinstance(pet_id, str) or not (1 <= len(pet_id) <= MAX_PET_ID_LEN) or not _RE_PET_ID.match(pet_id):
        raise ProtocolError(ERR_INVALID_ENVELOPE, "pet_id 非法")

    seq = message["seq"]
    if not _is_int(seq) or seq < 0:
        raise ProtocolError(ERR_INVALID_ENVELOPE, "seq 必须是非负整数")

    request_id = message["request_id"]
    if kind == KIND_EVENT:
        if request_id is not None:
            raise ProtocolError(ERR_INVALID_ENVELOPE, "event 的 request_id 必须是 null")
    else:
        if (
            not isinstance(request_id, str)
            or not (1 <= len(request_id) <= MAX_REQUEST_ID_LEN)
            or not _RE_REQUEST_ID.match(request_id)
        ):
            raise ProtocolError(ERR_INVALID_ENVELOPE, f"{kind} 的 request_id 必须是非空字符串")

    if message_type not in KNOWN_TYPES:
        raise UnknownMessageType(message_type)

    expected_kind = PAYLOAD_SPECS[message_type]["kind"]
    if expected_kind is not None and kind != expected_kind:
        raise ProtocolError(ERR_INVALID_ENVELOPE, f"{message_type} 要求 kind={expected_kind}")

    _validate_payload(message_type, message["payload"])
    return message


# ----------------------------------------------------------------------
# 出向信封构造
# ----------------------------------------------------------------------
def make_envelope(
    *,
    session_id: str,
    seq: int,
    kind: str,
    message_type: str,
    payload: dict | None = None,
    request_id: str | None = None,
    pet_id: str = DEFAULT_PET_ID,
) -> dict:
    """构造出向信封。

    ``event`` 强制 ``request_id=None``；``request``/``response`` 必须显式给出
    ``request_id``——request/response 的关联关系一旦靠猜就会丢回执。
    """
    if kind == KIND_EVENT:
        if request_id is not None:
            raise ValueError("event 的 request_id 必须为 None")
    elif not request_id:
        raise ValueError(f"{kind} 必须提供 request_id")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
        "seq": seq,
        "kind": kind,
        "type": message_type,
        "pet_id": pet_id,
        "request_id": request_id,
        "payload": payload if payload is not None else {},
    }


def clamp_bubble(bubble: dict | None) -> dict | None:
    """把气泡裁剪进 schema 允许的范围，避免因越界被整条拒绝。"""
    if not bubble:
        return None
    text = str(bubble.get("text") or "").strip()
    if not text:
        return None
    duration = bubble.get("duration_ms", DEFAULT_BUBBLE_DURATION_MS)
    if not _is_int(duration):
        duration = DEFAULT_BUBBLE_DURATION_MS
    duration = max(MIN_BUBBLE_DURATION_MS, min(MAX_BUBBLE_DURATION_MS, duration))
    return {
        "text": text[:MAX_BUBBLE_TEXT_LEN],
        "important": bool(bubble.get("important", False)),
        "duration_ms": duration,
    }


def clamp_ttl_ms(ttl_ms: Any) -> int:
    if not _is_int(ttl_ms):
        return DEFAULT_PROPOSAL_TTL_MS
    return max(MIN_TTL_MS, min(MAX_TTL_MS, ttl_ms))
