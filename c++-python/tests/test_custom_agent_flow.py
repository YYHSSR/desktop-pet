# -*- coding: utf-8 -*-
"""CustomAgent 全链路：真实 JSONL 写入 → 状态事件 + 行为建议 → 结果回执。

事件样例以旧版 ``python/tests/test_agent_link.py`` 为基准：
未知事件忽略、历史不重放、半行不当整行。
"""

from __future__ import annotations

import json
import time
import uuid

import pytest


def make_config(events_path) -> dict:
    return {
        "revision": 1,
        "agent_backend": "python",
        "agents": {
            "a1": {
                "enabled": True,
                "kind": "custom",
                "display_name": "集成测试Agent",
                "events_path": str(events_path),
                "thinking_text": "想想……",
            }
        },
    }


def make_pet(generation: int = 7) -> dict:
    return {
        "generation": generation,
        "character": "random",
        "visible": True,
        "interaction_locked": False,
        "actions": ["random/写代码", "click/招手", "system/idle", "system/bubble"],
    }


def append_events(path, *events) -> None:
    with open(path, "ab") as handle:
        for event in events:
            if isinstance(event, bytes):
                handle.write(event)
            elif isinstance(event, str):
                handle.write(event.encode("utf-8"))
            else:
                handle.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))


def test_full_flow_state_then_proposal_then_receipt(host, tmp_path) -> None:
    """真实 JSONL → agent.state_changed + behavior.propose → behavior.result 被收下。"""
    events = tmp_path / "events.jsonl"
    events.write_bytes(b"")
    host.prepare(make_config(events), make_pet())

    append_events(events, {"event": "PreToolUse", "tool": "bash"})
    state = host.wait_for(
        lambda m: m.get("type") == "agent.state_changed"
        and m.get("payload", {}).get("agent") == "a1"
        and m.get("payload", {}).get("state") == "working",
        timeout=15,
    )
    assert state["payload"]["tool"] == "bash"
    assert state["payload"]["generation"] == 7

    proposal = host.of_type("behavior.propose", timeout=15)
    assert proposal["payload"]["generation"] == 7
    assert proposal["payload"]["config_revision"] == 1
    assert proposal["kind"] == "request"
    # 动作必须在白名单内
    assert proposal["payload"]["action_id"] in make_pet()["actions"]

    # 回执被收下：通道保持健康（后续 ping 仍有 pong）
    host.send(
        "behavior.result",
        {"status": "accepted"},
        kind="response",
        request_id=proposal["request_id"],
    )
    time.sleep(0.5)
    ping = host.send("runtime.ping", kind="request", request_id="health-1")
    host.wait_for(lambda m: m.get("request_id") == "health-1" and m.get("type") == "runtime.pong", timeout=5)


def test_history_is_never_replayed(host, tmp_path) -> None:
    """配置下发前文件里的历史内容是「过去」，重放等于把过去的事件当现在播。"""
    events = tmp_path / "events.jsonl"
    append_events(events, {"event": "PreToolUse", "tool": "old_tool"}, {"event": "Stop"})
    host.prepare(make_config(events), make_pet())

    append_events(events, {"event": "Stop"})
    state = host.wait_for(
        lambda m: m.get("type") == "agent.state_changed" and m.get("payload", {}).get("agent") == "a1",
        timeout=15,
    )
    assert state["payload"]["state"] == "attention", "Stop 归一化为 attention"
    tools = [m["payload"].get("tool", "") for m in host.messages if m.get("type") == "agent.state_changed"]
    assert "old_tool" not in tools, "历史事件不得回放"


def test_unknown_and_broken_lines_are_ignored(host, tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_bytes(b"")
    host.prepare(make_config(events), make_pet())

    append_events(
        events,
        "not json at all",
        b'{"broken":\n',
        {"event": "某Agent私有事件"},
        [1, 2, 3],
    )
    time.sleep(3.0)
    states = [m for m in host.messages if m.get("type") == "agent.state_changed"]
    assert states == [], "无法识别的事件必须被忽略"

    # 坏行不影响后续合法行
    append_events(events, {"state": "thinking"})
    state = host.wait_for(
        lambda m: m.get("type") == "agent.state_changed" and m.get("payload", {}).get("state") == "thinking",
        timeout=15,
    )
    assert state["payload"]["agent"] == "a1"


def test_half_line_is_not_parsed_until_complete(host, tmp_path) -> None:
    """半行缓冲：写入分两次到达，第一次的半行绝不能被当成事件解析。"""
    events = tmp_path / "events.jsonl"
    events.write_bytes(b"")
    host.prepare(make_config(events), make_pet())

    append_events(events, b'{"state": "work')
    time.sleep(2.5)
    assert not [m for m in host.messages if m.get("type") == "agent.state_changed"]

    append_events(events, b'ing"}\n')
    host.wait_for(
        lambda m: m.get("type") == "agent.state_changed" and m.get("payload", {}).get("state") == "working",
        timeout=15,
    )


def test_generation_mismatch_proposal_is_still_replyable(host, tmp_path) -> None:
    """新代次事件丢弃待处理建议（Worker 侧），回执通道不受影响。"""
    events = tmp_path / "events.jsonl"
    events.write_bytes(b"")
    host.prepare(make_config(events), make_pet(generation=7))

    append_events(events, {"event": "PreToolUse", "tool": "bash"})
    host.of_type("behavior.propose", timeout=15)

    # 代次推进（宿主在拖拽开始时递增）：旧建议立即作废
    host.send("pet.event", {"name": "drag_start", "generation": 8})
    host.send("pet.snapshot", dict(make_pet(generation=8), interaction_locked=True))
    time.sleep(0.5)
    ping = host.send("runtime.ping", kind="request", request_id=f"after-gen-{uuid.uuid4().hex[:6]}")
    host.wait_for(lambda m: m.get("request_id") == ping["request_id"], timeout=5)


def test_disabled_agent_is_not_monitored(host, tmp_path) -> None:
    config = make_config(tmp_path / "events.jsonl")
    config["agents"]["a1"]["enabled"] = False
    (tmp_path / "events.jsonl").write_bytes(b"")
    host.prepare(config, make_pet())

    append_events(tmp_path / "events.jsonl", {"event": "PreToolUse", "tool": "bash"})
    time.sleep(2.5)
    assert not [m for m in host.messages if m.get("type") == "agent.state_changed"]
