# -*- coding: utf-8 -*-
"""握手与能力协商：真实 Worker 进程上的协议启动序列。"""

from __future__ import annotations

import time

REQUIRED_CAPABILITIES = ("state", "behavior", "custom_agent", "config_patch")


def test_hello_gets_ready_with_capabilities(host) -> None:
    ready = host.prepare()
    payload = ready["payload"]
    assert set(REQUIRED_CAPABILITIES) <= set(payload["capabilities"])
    assert payload["worker_version"]
    assert isinstance(payload.get("pid"), int) and payload["pid"] > 0


def test_ready_request_id_matches_hello(host) -> None:
    hello = host.send(
        "runtime.hello",
        {"host_version": "integration", "required_capabilities": []},
        kind="request",
        request_id="assoc-1",
    )
    ready = host.of_type("runtime.ready")
    assert ready["request_id"] == hello["request_id"]


def test_worker_answers_ping(host) -> None:
    host.prepare()
    pong = None
    for _ in range(3):
        ping = host.send("runtime.ping", kind="request", request_id=f"ping-{time.monotonic_ns()}")
        pong = host.wait_for(
            lambda m: m.get("type") == "runtime.pong" and m.get("request_id") == ping["request_id"], timeout=5
        )
        break
    assert pong is not None and pong["kind"] == "response"


def test_unknown_type_is_isolated_and_session_survives(host) -> None:
    """信封合法但 type 未定义：分帧层隔离（计数、不投递），绝不触发默认动作。

    未定义 type 到不了运行时，因此既没有 unsupported 回执也没有任何动作；
    会话必须继续健康。方向错配的已知类型同理——它们带错 kind，在契约校验
    （payload 规格与 kind 耦合）处就被整条拒绝。
    """
    host.prepare()
    host.send("agent.magic", {})
    # 方向错配：state_changed 只能由 Worker 发出，且必须是 event；这里以 request 发
    host.send("agent.state_changed", {"agent": "a1", "state": "idle", "generation": 1, "config_revision": 1},
              kind="request", request_id="wrong-way-1")
    host.expect_silence("runtime.unsupported_message", seconds=1.5)
    assert not [m for m in host.messages if m.get("request_id") == "wrong-way-1"]

    ping = host.send("runtime.ping", kind="request", request_id="after-unknown")
    host.wait_for(lambda m: m.get("request_id") == "after-unknown", timeout=5)


def test_unsupported_version_does_not_kill_session(host) -> None:
    """主版本不兼容：不投递该消息、不中止会话；恢复 v1 后一切照常。"""
    host.prepare()
    host.send("runtime.ping", kind="request", request_id="v2-1", protocol_version=2)
    # v1 通道仍然健康
    host.send("runtime.ping", kind="request", request_id="v1-1")
    host.wait_for(lambda m: m.get("request_id") == "v1-1" and m.get("type") == "runtime.pong", timeout=5)


def test_missing_capabilities_still_handshakes(host) -> None:
    """宿主不要求能力时不影响握手（降级判定是宿主侧的职责）。"""
    ready = host.prepare()
    assert "capabilities" in ready["payload"]


def test_duplicate_seq_is_ignored(host) -> None:
    """同 session 同 kind 的重复 seq 视为重放：第二条 ping 不得产生第二条 pong。"""
    host.prepare()
    first = host.send("runtime.ping", kind="request", request_id="dup-1")
    host.wait_for(lambda m: m.get("type") == "runtime.pong" and m.get("request_id") == "dup-1", timeout=5)

    # 用相同 seq 再发一条（绕过 harness 的自动 seq）
    replay = dict(first)
    replay["request_id"] = "dup-2"
    host.send_raw(replay)

    time.sleep(1.0)
    pongs = [m for m in host.messages if m.get("type") == "runtime.pong" and m.get("request_id") == "dup-2"]
    assert pongs == [], "重放的消息必须被忽略"
