# -*- coding: utf-8 -*-
"""Runtime 协议状态机测试：握手、快照、出向消息与退出路径。

不启动子进程（那些属于 ``c++-python/tests`` 的跨进程集成测试），只验证「给定一串
合法入向消息，Worker 会发出什么」——这一层是两端最容易对不上的地方。
"""

from __future__ import annotations

import pytest
from _support import FakeClock, MemoryStream, envelope

from pet_worker.contracts import (
    KIND_EVENT,
    KIND_REQUEST,
    KIND_RESPONSE,
    MAX_BUBBLE_TEXT_LEN,
    MAX_TTL_MS,
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
    SYSTEM_ACTION_IDLE,
)
from pet_worker.runtime import Runtime
from pet_worker.scheduler import Scheduler
from pet_worker.transport import LineWriter, OutboundQueue

SESSION = "sess-0001"
ACTIONS = ("rand/写代码", "rand/轻快记录")


def make_runtime(*, outbound: OutboundQueue | None = None) -> tuple[Runtime, MemoryStream, FakeClock, Scheduler]:
    clock = FakeClock()
    scheduler = Scheduler(clock)
    stream = MemoryStream()
    runtime = Runtime(
        LineWriter(stream),
        scheduler,
        outbound=outbound,
        clock=clock,
        wall_clock=clock,
        heartbeat_interval_s=2.0,
    )
    return runtime, stream, clock, scheduler


def handshake(runtime: Runtime, session: str = SESSION, **payload) -> None:
    runtime.handle(
        envelope(MSG_RUNTIME_HELLO, {"host_version": "0.2.3", **payload}, kind=KIND_REQUEST, session_id=session)
    )


def sync_snapshots(
    runtime: Runtime,
    *,
    session: str = SESSION,
    backend: str = "python",
    revision: int = 12,
    generation: int = 7,
    visible: bool = True,
) -> None:
    runtime.handle(
        envelope(
            MSG_CONFIG_SNAPSHOT,
            {
                "revision": revision,
                "agent_backend": backend,
                "agents": {
                    "a1": {
                        "enabled": True,
                        "kind": "custom",
                        "events_path": "a1.jsonl",
                        "display_name": "Claude",
                    }
                },
                "policy": {"notify_done": True},
            },
            kind=KIND_EVENT,
            session_id=session,
        )
    )
    runtime.handle(
        envelope(
            MSG_PET_SNAPSHOT,
            {
                "generation": generation,
                "character": "random",
                "visible": visible,
                "interaction_locked": False,
                "actions": list(ACTIONS),
            },
            kind=KIND_EVENT,
            session_id=session,
        )
    )


def ready_runtime(**kwargs) -> tuple[Runtime, MemoryStream, FakeClock, Scheduler]:
    runtime, stream, clock, scheduler = make_runtime(**kwargs)
    handshake(runtime)
    sync_snapshots(runtime)
    # 握手会入队一条 runtime.ready：先冲掉，后续断言才只看到被测行为发出来的东西。
    runtime.flush()
    stream.chunks.clear()
    return runtime, stream, clock, scheduler


def pet_event(name: str, generation: int = 7, **extra) -> dict:
    return envelope(MSG_PET_EVENT, {"name": name, "generation": generation, **extra}, kind=KIND_EVENT)


# ----------------------------------------------------------------------
# 握手与快照
# ----------------------------------------------------------------------
def test_nothing_is_sent_before_handshake() -> None:
    runtime, stream, _, _ = make_runtime()
    # 没有 session_id 连合法信封都构造不出来：只能拒绝，不能编一个。
    assert runtime.emit_state_changed("a1", "working") is False
    runtime.flush()
    assert stream.envelopes() == []


def test_hello_emits_ready_with_matching_request_id() -> None:
    runtime, stream, _, _ = make_runtime()
    handshake(runtime)
    runtime.flush()

    ready = stream.envelopes()[-1]
    assert ready["type"] == MSG_RUNTIME_READY
    assert ready["kind"] == KIND_RESPONSE
    assert ready["request_id"] == "runtime.hello-req"
    assert ready["session_id"] == SESSION
    assert ready["seq"] == 0
    assert ready["payload"]["worker_version"]
    assert ready["payload"]["pid"] > 0
    assert set(ready["payload"]["capabilities"]) >= {"state", "behavior", "custom_agent", "config_patch"}
    assert runtime.ready is True


def test_missing_capabilities_do_not_fail_handshake() -> None:
    runtime, _, _, _ = make_runtime()
    handshake(runtime, required_capabilities=["未来能力"])
    assert runtime.ready is True  # 由宿主决定是否降级，Worker 只如实回报


def test_snapshots_are_applied_with_previous_view() -> None:
    runtime, _, _, _ = make_runtime()
    handshake(runtime)
    seen: list = []
    runtime.on_config_changed = lambda previous, current: seen.append(("cfg", previous.revision, current.revision))
    runtime.on_pet_snapshot = lambda previous, current: seen.append(("pet", previous.generation, current.generation))

    sync_snapshots(runtime, revision=12, generation=7)
    sync_snapshots(runtime, revision=13, generation=8)
    assert seen == [("cfg", 0, 12), ("pet", 0, 7), ("cfg", 12, 13), ("pet", 7, 8)]
    assert runtime.snapshots_applied is True
    assert runtime.config.agents["a1"].display_name == "Claude"
    assert runtime.pet.actions == ACTIONS


def test_emit_requires_both_snapshots() -> None:
    runtime, _, _, _ = make_runtime()
    handshake(runtime)
    assert runtime.emit_state_changed("a1", "working") is False  # 动作白名单还未知
    sync_snapshots(runtime)
    assert runtime.emit_state_changed("a1", "working") is True


def test_invalid_state_raises() -> None:
    runtime, _, _, _ = ready_runtime()
    with pytest.raises(ValueError):
        runtime.emit_state_changed("a1", "dancing")


def test_state_changes_are_deduped_in_queue() -> None:
    """同一 Agent 的连续状态只留最新一条：队列不该被状态抖动灌满。"""
    runtime, stream, _, _ = ready_runtime()
    runtime.emit_state_changed("a1", "working")
    runtime.emit_state_changed("a1", "thinking")
    assert runtime.outbound_pending == 1

    runtime.flush()
    events = [item for item in stream.envelopes() if item["type"] == MSG_AGENT_STATE_CHANGED]
    assert len(events) == 1
    assert events[0]["kind"] == KIND_EVENT
    assert events[0]["request_id"] is None
    assert events[0]["payload"] == {
        "agent": "a1",
        "state": "thinking",
        "generation": 7,
        "config_revision": 12,
    }


def test_state_change_carries_tool_and_monotonic_seq() -> None:
    runtime, stream, _, _ = ready_runtime()
    runtime.emit_state_changed("a1", "working", "bash")
    runtime.emit_behavior_propose(ACTIONS[0])
    runtime.flush()

    envelopes = stream.envelopes()
    assert envelopes[0]["payload"]["tool"] == "bash"
    seqs = [item["seq"] for item in envelopes]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# ----------------------------------------------------------------------
# 行为建议
# ----------------------------------------------------------------------
def test_proposal_rejects_action_outside_catalog() -> None:
    runtime, _, _, _ = ready_runtime()
    assert runtime.emit_behavior_propose("rand/不存在的动作") is None
    assert runtime.pending_proposals == 0


def test_proposal_allows_system_action_and_clamps_payload() -> None:
    runtime, stream, _, _ = ready_runtime()
    assert runtime.emit_behavior_propose(SYSTEM_ACTION_IDLE) is not None

    rid = runtime.emit_behavior_propose(
        ACTIONS[0],
        agent="a1",
        bubble={"text": "x" * 500, "important": True, "duration_ms": 999999},
        ttl_ms=999999,
    )
    assert rid is not None
    runtime.flush()

    proposals = [item for item in stream.envelopes() if item["type"] == MSG_BEHAVIOR_PROPOSE]
    proposal = proposals[-1]
    assert proposal["kind"] == KIND_REQUEST
    assert proposal["request_id"] == rid
    payload = proposal["payload"]
    assert payload["action_id"] == ACTIONS[0]
    assert payload["agent"] == "a1"
    assert (payload["generation"], payload["config_revision"]) == (7, 12)
    assert payload["ttl_ms"] == MAX_TTL_MS
    assert len(payload["bubble"]["text"]) == MAX_BUBBLE_TEXT_LEN
    # 两条建议都要留在队列里：宿主拒绝一条不代表另一条可以消失。
    assert runtime.stats.proposals_sent == 2
    assert runtime.pending_proposals == 2


def test_proposal_without_bubble_omits_field() -> None:
    runtime, stream, _, _ = ready_runtime()
    runtime.emit_behavior_propose(ACTIONS[0])
    runtime.flush()
    assert "bubble" not in stream.envelopes()[-1]["payload"]


def test_empty_action_id_is_a_programming_error() -> None:
    runtime, _, _, _ = ready_runtime()
    with pytest.raises(ValueError):
        runtime.emit_behavior_propose("")


def test_proposal_expires_without_receipt() -> None:
    runtime, _, clock, scheduler = ready_runtime()
    runtime.emit_behavior_propose(ACTIONS[0], ttl_ms=2000)
    assert runtime.pending_proposals == 1

    clock.advance(3.0)
    scheduler.tick()
    assert runtime.pending_proposals == 0
    assert runtime.stats.proposals_expired == 1


def test_behavior_result_clears_pending() -> None:
    runtime, _, _, _ = ready_runtime()
    rid = runtime.emit_behavior_propose(ACTIONS[0])
    runtime.handle(envelope(MSG_BEHAVIOR_RESULT, {"status": "accepted"}, kind=KIND_RESPONSE, request_id=rid))
    assert (runtime.pending_proposals, runtime.stats.proposals_accepted) == (0, 1)

    rid = runtime.emit_behavior_propose(ACTIONS[0])
    runtime.handle(
        envelope(
            MSG_BEHAVIOR_RESULT,
            {"status": "rejected", "reason": "interaction_locked"},
            kind=KIND_RESPONSE,
            request_id=rid,
        )
    )
    assert (runtime.pending_proposals, runtime.stats.proposals_rejected) == (0, 1)


def test_config_patch_request_carries_revision() -> None:
    runtime, stream, _, _ = ready_runtime()
    seen: list = []
    runtime.on_config_patch_result = lambda rid, payload: seen.append((rid, payload["status"]))

    rid = runtime.emit_config_patch({"notify_done": False})
    assert rid is not None
    runtime.flush()
    request = stream.envelopes()[-1]
    assert request["type"] == MSG_CONFIG_PATCH_REQUEST
    assert request["payload"]["expected_revision"] == 12
    assert request["payload"]["patch"] == {"notify_done": False}

    runtime.handle(envelope(MSG_CONFIG_PATCH_RESULT, {"status": "applied"}, kind=KIND_RESPONSE, request_id=rid))
    assert seen == [(rid, "applied")]


# ----------------------------------------------------------------------
# 背压
# ----------------------------------------------------------------------
def test_critical_messages_hit_queue_limit_and_mark_unhealthy() -> None:
    """关键消息不得静默丢弃：队列满时拒绝并标记通道不健康，让宿主重连。"""
    outbound = OutboundQueue(max_messages=3, max_bytes=1 << 20)
    runtime, _, _, _ = ready_runtime(outbound=outbound)

    accepted = [runtime.emit_behavior_propose(ACTIONS[0]) for _ in range(5)]
    assert accepted[:3] == [rid for rid in accepted[:3] if rid is not None]
    assert accepted[3:] == [None, None]
    assert runtime.outbound_pending == 3
    assert runtime.channel_unhealthy is True


# ----------------------------------------------------------------------
# 心跳
# ----------------------------------------------------------------------
def test_ping_is_answered_with_pong() -> None:
    runtime, stream, _, _ = ready_runtime()
    runtime.handle(envelope(MSG_RUNTIME_PING, {}, kind=KIND_REQUEST, request_id="ping-1"))
    runtime.flush()

    pong = stream.envelopes()[-1]
    assert pong["type"] == MSG_RUNTIME_PONG
    assert pong["kind"] == KIND_RESPONSE
    assert pong["request_id"] == "ping-1"
    assert runtime.stats.pings_answered == 1


def test_pong_is_counted_only() -> None:
    runtime, stream, _, _ = ready_runtime()
    runtime.handle(envelope(MSG_RUNTIME_PONG, {}, kind=KIND_RESPONSE, request_id="ping-1"))
    runtime.flush()
    assert runtime.stats.pongs_received == 1
    assert stream.envelopes() == []


def test_host_silence_only_warns_and_never_exits() -> None:
    """退出条件只有 stdin EOF 与 shutdown：静默只告警，不当成失联自己退出。"""
    runtime, _, clock, scheduler = ready_runtime()
    clock.advance(60.0)
    scheduler.tick()
    assert runtime.stopping is False
    assert runtime.ready is True


# ----------------------------------------------------------------------
# 入向异常与 session
# ----------------------------------------------------------------------
def test_unknown_inbound_type_gets_unsupported_reply() -> None:
    runtime, stream, _, _ = ready_runtime()
    runtime.handle(envelope("vendor.实验消息", {}, kind=KIND_REQUEST, request_id="req-x"))
    runtime.flush()

    reply = stream.envelopes()[-1]
    assert reply["type"] == MSG_UNSUPPORTED_MESSAGE
    assert reply["request_id"] == "req-x"
    assert reply["payload"] == {"reason": "unknown_type", "detail": "vendor.实验消息"}
    assert runtime.stats.unsupported_in == 1


def test_unsupported_without_request_id_has_no_reply() -> None:
    """信封结构已坏时回执无从关联：与其编一个 request_id，不如只记日志。"""
    runtime, stream, _, _ = ready_runtime()
    runtime.handle({"type": "vendor.无id", "session_id": SESSION, "payload": {}})
    runtime.flush()
    assert stream.envelopes() == []
    assert runtime.stats.unsupported_in == 1


def test_inflight_messages_of_old_session_are_ignored() -> None:
    runtime, _, _, _ = ready_runtime()
    runtime.handle_inbound(
        envelope(MSG_PET_EVENT, {"name": "hidden", "generation": 7}, kind=KIND_EVENT, session_id="sess-old")
    )
    assert runtime.pet.visible is True


def test_new_session_resets_business_state() -> None:
    runtime, stream, _, _ = ready_runtime()
    runtime.emit_state_changed("a1", "working")
    stream.chunks.clear()

    handshake(runtime, session="sess-0002")
    runtime.flush()
    assert runtime.session_id == "sess-0002"
    assert runtime.snapshots_applied is False
    assert runtime.pet.generation == 0
    assert runtime.emit_state_changed("a1", "working") is False  # 必须等新快照
    ready = stream.envelopes()[-1]
    assert ready["type"] == MSG_RUNTIME_READY and ready["seq"] == 0 and ready["session_id"] == "sess-0002"


# ----------------------------------------------------------------------
# 宠物事件
# ----------------------------------------------------------------------
def test_pet_events_track_visibility_and_lock() -> None:
    runtime, _, _, _ = ready_runtime()
    runtime.handle(pet_event("drag_start"))
    assert runtime.pet.interaction_locked is True
    runtime.handle(pet_event("drag_end"))
    assert runtime.pet.interaction_locked is False

    runtime.handle(pet_event("hidden"))
    assert runtime.pet.visible is False
    runtime.handle(pet_event("shown"))
    assert runtime.pet.visible is True
    assert runtime.pet.generation == 7


def test_newer_generation_is_adopted_and_older_ignored() -> None:
    runtime, _, _, _ = ready_runtime()
    runtime.handle(pet_event("character_changed", generation=9))
    assert runtime.pet.generation == 9

    runtime.handle(pet_event("hidden", generation=8))
    assert runtime.pet.visible is True  # 旧代次事件不得改变当前状态


def test_generation_bumping_events_drop_pending_proposals() -> None:
    runtime, _, _, _ = ready_runtime()
    runtime.emit_behavior_propose(ACTIONS[0])
    assert runtime.pending_proposals == 1

    runtime.handle(pet_event("drag_start"))
    assert runtime.pending_proposals == 0


def test_pet_event_callback_receives_name_and_payload() -> None:
    runtime, _, _, _ = ready_runtime()
    seen: list = []
    runtime.on_pet_event = lambda name, payload: seen.append((name, payload.get("generation")))
    runtime.handle(pet_event("action_finished", action_id=ACTIONS[0]))
    assert seen == [("action_finished", 7)]


# ----------------------------------------------------------------------
# 退出
# ----------------------------------------------------------------------
def test_stdin_eof_stops_the_runtime() -> None:
    runtime, _, _, _ = ready_runtime()
    runtime.handle_eof()
    assert runtime.stopping is True
    assert runtime.stop_reason == "stdin_eof"
    assert runtime.emit_state_changed("a1", "working") is False


def test_shutdown_event_stops_the_runtime() -> None:
    runtime, _, _, scheduler = ready_runtime()
    seen: list = []
    runtime.on_stopping = seen.append

    runtime.handle(envelope(MSG_RUNTIME_SHUTDOWN, {"timeout_ms": 2000}, kind=KIND_EVENT))
    assert runtime.stop_reason == "shutdown"
    assert seen == ["shutdown"]
    assert scheduler.pending == 0  # 定时任务随停止一起清掉

    # 重复停止不改写原因，回调也只触发一次
    runtime.request_stop("signal:2")
    assert runtime.stop_reason == "shutdown"
    assert seen == ["shutdown"]


def test_health_snapshot_has_no_paths_or_payloads() -> None:
    """健康快照会进日志：绝不能带用户路径或整行 payload。"""
    runtime, _, _, _ = ready_runtime()
    runtime.emit_state_changed("a1", "working")
    health = runtime.health()
    assert health["session"] == SESSION
    assert health["agents"] == 1
    assert health["backend"] == "python"
    assert "a1.jsonl" not in repr(health)
    assert set(health) >= {"messages_in", "messages_out", "proposals_sent", "state_changes_sent"}
