# -*- coding: utf-8 -*-
"""Worker 运行时：握手、快照、心跳、出向消息构造与生命周期。

不变量：
- **一个写入者**。所有出向消息都由本模块在同一线程里构造并入队；
- **session_id 由宿主给**。宿主在 ``runtime.hello`` 里给出本次 Worker 实例的
  session_id，Worker 采纳它并用于之后所有出向消息。这样「旧 session 的消息直接
  忽略」在两端都成立：重启换 session，在途的旧消息自然失效；
- **握手之前不说话**。没有 session_id 就没有合法的信封可发；
- **快照之前不建议**。``config.snapshot`` 与 ``pet.snapshot`` 都到位后，
  才允许产出 ``behavior.propose``——否则建议里的 generation/revision 是编的。

存活检查是**宿主驱动**的：宿主按心跳间隔发 ``runtime.ping``，Worker 回
``runtime.pong``。Worker 不主动 ping——超时判定只能有一个源头，否则两端会各自
算出不同的失联时刻。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pet_worker import __version__
from pet_worker.contracts import (
    AGENT_BACKENDS,
    CAPABILITIES,
    DEFAULT_PET_ID,
    DEFAULT_PROPOSAL_TTL_MS,
    HEARTBEAT_INTERVAL_MS,
    HEARTBEAT_MISS_THRESHOLD,
    INBOUND_TYPES,
    KIND_EVENT,
    KIND_REQUEST,
    KIND_RESPONSE,
    MSG_AGENT_STATE_CHANGED,
    MSG_BEHAVIOR_PROPOSE,
    MSG_BEHAVIOR_RESULT,
    MSG_CONFIG_PATCH_REQUEST,
    MSG_CONFIG_PATCH_RESULT,
    MSG_CONFIG_SNAPSHOT,
    MSG_PET_EVENT,
    MSG_PET_SNAPSHOT,
    MSG_RUNTIME_HELLO,
    MSG_RUNTIME_PING,
    MSG_RUNTIME_PONG,
    MSG_RUNTIME_READY,
    MSG_RUNTIME_SHUTDOWN,
    MSG_UNSUPPORTED_MESSAGE,
    SYSTEM_ACTIONS,
    VALID_STATES,
    clamp_bubble,
    clamp_ttl_ms,
    make_envelope,
)
from pet_worker.logsetup import get_logger
from pet_worker.scheduler import Scheduler, Task
from pet_worker.transport import LineWriter, OutboundQueue

log = get_logger("runtime")

#: 维护节拍（1s）下多久打一次健康快照：够定位问题，又不至于把 stderr 刷满。
HEALTH_LOG_INTERVAL_TICKS = 60

__all__ = ["Runtime", "PetView", "ConfigView", "AgentConfig", "PolicyConfig", "RuntimeStats"]


# ----------------------------------------------------------------------
# 只读视图
# ----------------------------------------------------------------------
@dataclass
class AgentConfig:
    """``config.snapshot`` 里单个 Agent 的只读配置。"""

    key: str
    enabled: bool = False
    kind: str = "custom"
    display_name: str = ""
    events_path: str = ""
    busy_agents: tuple[str, ...] = ()
    thinking_text: str = ""

    @property
    def display(self) -> str:
        return self.display_name or self.key


@dataclass
class PolicyConfig:
    """业务策略参数。默认值与旧版 ``AgentLinkManager`` 常量一致。

    宿主可以只发关心的一两项，其余走默认；也完全可以不发 ``policy``，
    此时 Worker 的行为与旧版 Python 实现对齐。
    """

    notify_state: bool = False
    notify_done: bool = True
    notify_activity: bool = False
    min_interval_ms: int = 2000
    activity_interval_ms: int = 10000
    activity_global_min_ms: int = 8000
    activity_same_label_ms: int = 60000
    done_confirm_ms: int = 800
    done_cooldown_ms: int = 5000

    @classmethod
    def from_payload(cls, raw: Any) -> "PolicyConfig":
        """从 payload 里挑出认识的键；不认识或不合法的一律忽略。

        契约校验已经把类型挡住了，这里再兜一层是为了「宿主版本比 Worker 新」时
        不要因为多一个字段就整份配置作废——策略参数不是安全边界。
        """
        if not isinstance(raw, dict):
            return cls()
        values: dict[str, Any] = {}
        for name in ("notify_state", "notify_done", "notify_activity"):
            if isinstance(raw.get(name), bool):
                values[name] = raw[name]
        for name in (
            "min_interval_ms",
            "activity_interval_ms",
            "activity_global_min_ms",
            "activity_same_label_ms",
            "done_confirm_ms",
            "done_cooldown_ms",
        ):
            item = raw.get(name)
            if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                values[name] = item
        return cls(**values)


@dataclass
class ConfigView:
    revision: int = 0
    agent_backend: str = ""
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    applied: bool = False

    @property
    def manages_agents(self) -> bool:
        """只有后端为 python 时，本 Worker 才被允许驱动桌宠。"""
        return self.agent_backend == "python"


@dataclass
class PetView:
    generation: int = 0
    character: str = ""
    visible: bool = False
    interaction_locked: bool = False
    actions: tuple[str, ...] = ()
    applied: bool = False


@dataclass
class RuntimeStats:
    messages_in: int = 0
    messages_out: int = 0
    unsupported_in: int = 0
    proposals_sent: int = 0
    proposals_accepted: int = 0
    proposals_rejected: int = 0
    proposals_expired: int = 0
    state_changes_sent: int = 0
    pings_answered: int = 0
    pongs_received: int = 0
    last_inbound_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "messages_in": self.messages_in,
            "messages_out": self.messages_out,
            "unsupported_in": self.unsupported_in,
            "proposals_sent": self.proposals_sent,
            "proposals_accepted": self.proposals_accepted,
            "proposals_rejected": self.proposals_rejected,
            "proposals_expired": self.proposals_expired,
            "state_changes_sent": self.state_changes_sent,
            "pings_answered": self.pings_answered,
            "pongs_received": self.pongs_received,
        }


def _normalize_path(raw: str) -> str:
    """展开 ``~`` 并转成绝对路径。只读写用户给定的位置，绝不替用户创建目录。"""
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(raw))


def _request_id_of(message: dict) -> str:
    value = message.get("request_id")
    return str(value) if isinstance(value, str) else ""


class Runtime:
    """协议状态机与出向消息工厂。"""

    def __init__(
        self,
        writer: LineWriter,
        scheduler: Scheduler,
        *,
        outbound: OutboundQueue | None = None,
        pet_id: str = DEFAULT_PET_ID,
        capabilities: tuple[str, ...] = CAPABILITIES,
        worker_version: str = __version__,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_MS / 1000.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._writer = writer
        self._scheduler = scheduler
        self._outbound = outbound if outbound is not None else OutboundQueue()
        self._pet_id = pet_id
        self._capabilities = tuple(capabilities)
        self._worker_version = worker_version
        self._heartbeat_interval_s = heartbeat_interval_s
        self._clock = clock
        self._wall_clock = wall_clock

        self._session_id = ""
        self._seq = 0
        self._ready = False
        self._stopping = False
        self._stop_reason = ""
        self._request_counter = 0
        self._maintenance_task: Task | None = None
        self._miss_warned = False
        self._health_ticks = 0
        #: request_id → 到期时刻（monotonic）。用于统计过期建议，别让 TTL 变成摆设。
        self._pending_proposals: dict[str, float] = {}

        self.config = ConfigView()
        self.pet = PetView()
        self.stats = RuntimeStats()

        # 装配层注入的回调。全部可选，缺省即「只做协议，不做业务」。
        self.on_ready: Callable[[], None] | None = None
        self.on_config_changed: Callable[[ConfigView, ConfigView], None] | None = None
        self.on_pet_snapshot: Callable[[PetView, PetView], None] | None = None
        self.on_pet_event: Callable[[str, dict], None] | None = None
        self.on_behavior_result: Callable[[str, dict], None] | None = None
        self.on_config_patch_result: Callable[[str, dict], None] | None = None
        self.on_stopping: Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # 只读状态
    # ------------------------------------------------------------------
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    @property
    def snapshots_applied(self) -> bool:
        return self.config.applied and self.pet.applied

    @property
    def pending_proposals(self) -> int:
        return len(self._pending_proposals)

    @property
    def outbound_pending(self) -> int:
        return self._outbound.pending

    @property
    def channel_unhealthy(self) -> bool:
        return self._outbound.unhealthy

    def health(self) -> dict[str, Any]:
        """采样用健康快照。禁止把整行 payload 或用户路径打进去。"""
        return {
            "session": self._session_id,
            "ready": self._ready,
            "stopping": self._stopping,
            "generation": self.pet.generation,
            "revision": self.config.revision,
            "backend": self.config.agent_backend,
            "agents": len(self.config.agents),
            "pending_proposals": len(self._pending_proposals),
            "outbound_pending": self._outbound.pending,
            "outbound_bytes": self._outbound.pending_bytes,
            "channel_unhealthy": self._outbound.unhealthy,
            **self.stats.as_dict(),
        }

    # ------------------------------------------------------------------
    # 入向
    # ------------------------------------------------------------------
    def handle(self, message: dict) -> None:
        """处理一条已通过契约校验的入向消息。"""
        message_type = str(message.get("type"))
        self.stats.messages_in += 1
        self.stats.last_inbound_at = self._wall_clock()

        if message_type not in INBOUND_TYPES:
            # 信封合法但方向不对（例如宿主误发了一个只该由 Worker 发的类型）。
            self.stats.unsupported_in += 1
            self._send_unsupported(
                _request_id_of(message),
                "unknown_type",
                message_type,
                session_id=str(message.get("session_id") or ""),
            )
            return

        _HANDLERS[message_type](self, message)

    def handle_inbound(self, message: dict) -> None:
        """握手后处理：忽略非当前 session 的消息（重启后到达的在途旧消息）。"""
        session = str(message.get("session_id") or "")
        if self._session_id and session != self._session_id:
            log.debug("忽略非当前 session 的消息: %s", message.get("type"))
            return
        self.handle(message)

    # -- 各类型 --------------------------------------------------------
    def _on_hello(self, message: dict) -> None:
        payload = message.get("payload") or {}
        session = str(message.get("session_id") or "")
        if not self._session_id:
            self._session_id = session
        elif session != self._session_id:
            # 宿主重新握手：视为一次新会话，清掉所有与旧会话绑定的状态。
            log.info("收到新 session 握手 [%s]，重置会话状态", session)
            self._reset_session(session)

        host_version = str(payload.get("host_version") or "")
        required = tuple(str(item) for item in (payload.get("required_capabilities") or []))
        missing = [name for name in required if name not in self._capabilities]
        if missing:
            # 不代表失败：由宿主决定是否降级。Worker 如实回报能力即可。
            log.warning("宿主必需能力未全部提供: %s", ",".join(missing))

        log.info("握手完成: host_version=%s session=%s", host_version, self._session_id)
        self._send(
            KIND_RESPONSE,
            MSG_RUNTIME_READY,
            {
                "worker_version": self._worker_version,
                "capabilities": list(self._capabilities),
                "pid": os.getpid(),
            },
            request_id=_request_id_of(message),
        )
        self._ready = True
        self._start_timers()
        if self.on_ready is not None:
            self.on_ready()

    def _on_config_snapshot(self, message: dict) -> None:
        payload = message.get("payload") or {}
        backend = str(payload.get("agent_backend") or "")
        if backend not in AGENT_BACKENDS:
            return  # 契约校验已挡住，这里是兜底

        previous = self.config
        agents: dict[str, AgentConfig] = {}
        for key, raw in (payload.get("agents") or {}).items():
            if not isinstance(raw, dict):
                continue
            agents[str(key)] = AgentConfig(
                key=str(key),
                enabled=bool(raw.get("enabled", False)),
                kind=str(raw.get("kind") or "custom"),
                display_name=str(raw.get("display_name") or ""),
                events_path=_normalize_path(str(raw.get("events_path") or "")),
                busy_agents=tuple(str(item) for item in (raw.get("busy_agents") or [])),
                thinking_text=str(raw.get("thinking_text") or ""),
            )

        self.config = ConfigView(
            revision=int(payload.get("revision") or 0),
            agent_backend=backend,
            agents=agents,
            policy=PolicyConfig.from_payload(payload.get("policy")),
            applied=True,
        )
        log.info(
            "配置快照已应用: revision=%s backend=%s agents=%s",
            self.config.revision,
            backend,
            ",".join(sorted(agents)) or "-",
        )
        if self.on_config_changed is not None:
            self.on_config_changed(previous, self.config)

    def _on_pet_snapshot(self, message: dict) -> None:
        payload = message.get("payload") or {}
        previous = self.pet
        self.pet = PetView(
            generation=int(payload.get("generation") or 0),
            character=str(payload.get("character") or ""),
            visible=bool(payload.get("visible", False)),
            interaction_locked=bool(payload.get("interaction_locked", False)),
            actions=tuple(str(item) for item in (payload.get("actions") or [])),
            applied=True,
        )
        log.info(
            "宠物快照已应用: generation=%s character=%s actions=%s visible=%s locked=%s",
            self.pet.generation,
            self.pet.character or "-",
            len(self.pet.actions),
            self.pet.visible,
            self.pet.interaction_locked,
        )
        if self.on_pet_snapshot is not None:
            self.on_pet_snapshot(previous, self.pet)

    def _on_pet_event(self, message: dict) -> None:
        payload = message.get("payload") or {}
        name = str(payload.get("name") or "")
        generation = int(payload.get("generation") or 0)

        # 代次是强约束：旧代次的事件不得改变当前状态。
        if generation > self.pet.generation:
            self.pet.generation = generation
        elif generation < self.pet.generation:
            log.debug("忽略旧代次事件 %s (gen=%s < %s)", name, generation, self.pet.generation)
            return

        if name == "drag_start":
            self.pet.interaction_locked = True
        elif name == "drag_end":
            self.pet.interaction_locked = False
        elif name == "hidden":
            self.pet.visible = False
        elif name == "shown":
            self.pet.visible = True
        # character_changed / action_finished / click 只做通知，
        # 具体动作目录由紧随其后的 pet.snapshot 更新。

        if name in ("hidden", "drag_start", "character_changed"):
            # 这三类都会让「已发出的建议」失去意义；立刻作废，不给宿主制造过期动作。
            self._drop_pending_proposals("generation_changed")

        if self.on_pet_event is not None:
            self.on_pet_event(name, payload)

    def _on_behavior_result(self, message: dict) -> None:
        payload = message.get("payload") or {}
        request_id = _request_id_of(message)
        self._pending_proposals.pop(request_id, None)
        status = str(payload.get("status") or "")
        if status == "accepted":
            self.stats.proposals_accepted += 1
        else:
            self.stats.proposals_rejected += 1
        if self.on_behavior_result is not None:
            self.on_behavior_result(request_id, payload)

    def _on_config_patch_result(self, message: dict) -> None:
        payload = message.get("payload") or {}
        if self.on_config_patch_result is not None:
            self.on_config_patch_result(_request_id_of(message), payload)

    def _on_ping(self, message: dict) -> None:
        self.stats.pings_answered += 1
        self._send(KIND_RESPONSE, MSG_RUNTIME_PONG, {}, request_id=_request_id_of(message))

    def _on_pong(self, message: dict) -> None:
        self.stats.pongs_received += 1

    def _on_shutdown(self, message: dict) -> None:
        self._begin_stop("shutdown")

    # ------------------------------------------------------------------
    # 出向
    # ------------------------------------------------------------------
    def emit_state_changed(self, agent_key: str, state: str, tool: str = "") -> bool:
        """上报标准状态。仅用于展示与记录，不直接触发动画。

        同 Agent 的状态可合并：``dedupe_key`` 让队列里只留最新一条，
        Agent 高频抖动时不会把队列灌满。
        """
        if not self._can_emit():
            log.debug(
                "忽略状态上报（未就绪）: ready=%s config=%s pet=%s stopping=%s",
                self._ready,
                self.config.applied,
                self.pet.applied,
                self._stopping,
            )
            return False
        if state not in VALID_STATES:
            raise ValueError(f"非法状态: {state}")
        payload: dict[str, Any] = {
            "agent": agent_key,
            "state": state,
            "generation": self.pet.generation,
            "config_revision": self.config.revision,
        }
        if tool:
            payload["tool"] = tool[:128]
        ok = self._send(
            KIND_EVENT,
            MSG_AGENT_STATE_CHANGED,
            payload,
            dedupe_key=f"state:{agent_key}",
            droppable=True,
        )
        if ok:
            self.stats.state_changes_sent += 1
        return ok

    def emit_behavior_propose(
        self,
        action_id: str,
        *,
        agent: str | None = None,
        bubble: dict | None = None,
        ttl_ms: int = DEFAULT_PROPOSAL_TTL_MS,
        request_id: str | None = None,
    ) -> str | None:
        """提交行为建议。返回 ``request_id``；未就绪或队列拒绝时返回 ``None``。

        建议是关键消息：不能被静默合并且丢弃，否则「Agent 干完活」这类提示会消失。
        """
        if not self._can_emit():
            return None
        if not action_id:
            raise ValueError("action_id 不能为空")
        if action_id not in SYSTEM_ACTIONS and action_id not in self.pet.actions:
            # 不在当前角色动作白名单里（系统保留动作除外）：不发。
            # 宿主的 unknown_action 拒绝也是一次无意义的往返，不如在源头拦住。
            log.debug("动作不在白名单，丢弃建议: %s", action_id)
            return None

        rid = request_id or self._next_request_id("prop")
        ttl = clamp_ttl_ms(ttl_ms)
        payload: dict[str, Any] = {
            "action_id": action_id,
            "generation": self.pet.generation,
            "config_revision": self.config.revision,
            "ttl_ms": ttl,
        }
        if agent:
            payload["agent"] = agent
        safe_bubble = clamp_bubble(bubble)
        if safe_bubble:
            payload["bubble"] = safe_bubble

        if not self._send(KIND_REQUEST, MSG_BEHAVIOR_PROPOSE, payload, request_id=rid):
            return None
        self._pending_proposals[rid] = self._clock() + ttl / 1000.0
        self.stats.proposals_sent += 1
        return rid

    def emit_config_patch(self, patch: dict, *, expected_revision: int | None = None) -> str | None:
        """提交白名单配置修改请求。宿主校验 revision 后写盘。"""
        if not self._can_emit():
            return None
        rid = self._next_request_id("patch")
        payload = {
            "expected_revision": self.config.revision if expected_revision is None else int(expected_revision),
            "patch": dict(patch),
        }
        if not self._send(KIND_REQUEST, MSG_CONFIG_PATCH_REQUEST, payload, request_id=rid):
            return None
        return rid

    def flush(self) -> int:
        """把队列里的消息真正写到 stdout。返回写出的条数。"""
        written = 0
        for envelope in self._outbound.pop_all():
            if self._writer.write(envelope) is None:
                # 写失败几乎等同于管道已断：继续尝试只会重复失败。
                log.warning("协议写入失败，丢弃剩余 %s 条待发消息", self._outbound.pending)
                self._outbound.clear()
                break
            written += 1
        self.stats.messages_out += written
        return written

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def handle_eof(self) -> None:
        """stdin 关闭：宿主没了，Worker 必须退出，不能变成孤儿进程。"""
        self._begin_stop("stdin_eof")

    def request_stop(self, reason: str) -> None:
        self._begin_stop(reason)

    def shutdown_tasks(self) -> None:
        if self._maintenance_task is not None:
            self._scheduler.cancel(self._maintenance_task)
            self._maintenance_task = None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _reset_session(self, session_id: str) -> None:
        self._session_id = session_id
        self._seq = 0
        self._ready = False
        self._request_counter = 0
        self._pending_proposals.clear()
        self._miss_warned = False
        self.config = ConfigView()
        self.pet = PetView()
        self._outbound.clear()

    def _begin_stop(self, reason: str) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._stop_reason = reason
        log.info("开始停止: reason=%s", reason)
        if self.on_stopping is not None:
            try:
                self.on_stopping(reason)
            except Exception:
                log.exception("on_stopping 回调失败")
        self.shutdown_tasks()

    def _start_timers(self) -> None:
        if self._maintenance_task is None:
            # 1s 的维护节拍：过期建议回收 + 失联告警 + 周期健康日志。
            self._maintenance_task = self._scheduler.every(
                1.0, self._tick_maintenance, name="runtime.maintenance", immediate=False
            )

    def _tick_maintenance(self) -> None:
        self._expire_proposals()
        self._warn_if_host_silent()
        # 周期健康采样（采样而不是逐条记录：异常只在采样点上镜，日志才有可读性）。
        self._health_ticks += 1
        if self._health_ticks % HEALTH_LOG_INTERVAL_TICKS == 0:
            self.log_health()

    def _expire_proposals(self) -> None:
        now = self._clock()
        expired = [rid for rid, deadline in self._pending_proposals.items() if deadline <= now]
        for rid in expired:
            del self._pending_proposals[rid]
        if expired:
            self.stats.proposals_expired += len(expired)
            log.debug("回收 %s 条超期未回执的建议", len(expired))

    def _warn_if_host_silent(self) -> None:
        """宿主静默告警（只记日志，不退出）。

        退出条件只有一个：stdin EOF。把「多久没收到 ping」也当成失联会引入第二个
        超时源，和宿主自己算出的失联时刻对不上，反而更难排查。
        """
        if not self._ready or self.stats.last_inbound_at <= 0.0:
            return
        silence = self._wall_clock() - self.stats.last_inbound_at
        threshold = self._heartbeat_interval_s * HEARTBEAT_MISS_THRESHOLD
        if silence > threshold and not self._miss_warned:
            self._miss_warned = True
            log.warning("宿主已静默 %.1fs（阈值 %.1fs），通道可能已断", silence, threshold)
        elif silence <= threshold and self._miss_warned:
            self._miss_warned = False

    def _can_emit(self) -> bool:
        return bool(self._session_id) and self._ready and self.snapshots_applied and not self._stopping

    def _next_request_id(self, prefix: str) -> str:
        self._request_counter += 1
        return f"{prefix}-{self._request_counter}"

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq

    def _send(
        self,
        kind: str,
        message_type: str,
        payload: dict,
        *,
        request_id: str | None = None,
        dedupe_key: str | None = None,
        droppable: bool = False,
    ) -> bool:
        if not self._session_id:
            # 还没握手：没有合法信封可发（session_id 由宿主在 hello 里给出）。
            return False
        if kind == KIND_EVENT:
            request_id = None
        elif not request_id:
            request_id = self._next_request_id("req")

        envelope = make_envelope(
            session_id=self._session_id,
            seq=self._next_seq(),
            kind=kind,
            message_type=message_type,
            payload=payload,
            request_id=request_id,
            pet_id=self._pet_id,
        )
        return self._outbound.push(envelope, dedupe_key=dedupe_key, droppable=droppable)

    def _send_unsupported(self, request_id: str, reason: str, detail: str, *, session_id: str = "") -> None:
        """回一条 ``runtime.unsupported_message``。

        它本身是 response，必须有可关联的 request_id。若入向消息压根没给出
        request_id / session_id（例如信封结构已坏），就只有日志、没有回执——
        发一条 request_id 是编出来的消息，只会让宿主更难判断。
        """
        session = self._session_id or session_id
        if not session or not request_id:
            log.debug("无法回执 unsupported（缺少 session 或 request_id）: %s", detail)
            return
        payload = {"reason": reason, "detail": detail[:128]}
        if self._session_id:
            self._send(KIND_RESPONSE, MSG_UNSUPPORTED_MESSAGE, payload, request_id=request_id)
            return
        # 握手前的异常消息：用入向 session 回执，但不认为会话已建立。
        self._outbound.push(
            make_envelope(
                session_id=session,
                seq=self._next_seq(),
                kind=KIND_RESPONSE,
                message_type=MSG_UNSUPPORTED_MESSAGE,
                payload=payload,
                request_id=request_id[:64],
                pet_id=self._pet_id,
            )
        )

    def _drop_pending_proposals(self, reason: str) -> None:
        if not self._pending_proposals:
            return
        log.debug("作废 %s 条待回执建议: %s", len(self._pending_proposals), reason)
        self._pending_proposals.clear()

    def log_health(self) -> None:
        log.info("健康快照: %s", self.health())


# ----------------------------------------------------------------------
# 入向分发表
# ----------------------------------------------------------------------
_HANDLERS: dict[str, Callable[[Runtime, dict], None]] = {
    MSG_RUNTIME_HELLO: Runtime._on_hello,
    MSG_CONFIG_SNAPSHOT: Runtime._on_config_snapshot,
    MSG_PET_SNAPSHOT: Runtime._on_pet_snapshot,
    MSG_PET_EVENT: Runtime._on_pet_event,
    MSG_BEHAVIOR_RESULT: Runtime._on_behavior_result,
    MSG_CONFIG_PATCH_RESULT: Runtime._on_config_patch_result,
    MSG_RUNTIME_PING: Runtime._on_ping,
    MSG_RUNTIME_PONG: Runtime._on_pong,
    MSG_RUNTIME_SHUTDOWN: Runtime._on_shutdown,
}
