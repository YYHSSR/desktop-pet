# -*- coding: utf-8 -*-
"""Agent 侧纯逻辑测试：tailer 边界、事件归一化、监视器生命周期、装配层。

这里是「移植是否等价」的主要防线：旧版 ``python/tests/test_agent_link.py`` 覆盖过的
语义，用同一套输入再验证一遍，但**不导入旧模块**（导入它会拉起 PySide6）。
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from pathlib import Path

import pytest
from _support import FakeClock

import pet_worker
from pet_worker.agents.base import BaseAgentMonitor
from pet_worker.agents.custom_agent import CustomAgentMonitor
from pet_worker.agents.manager import AgentManager
from pet_worker.agents.protocol import extract_tool, normalize_event_state
from pet_worker.agents.tailer import ByteOffsetTailer
from pet_worker.runtime import AgentConfig, ConfigView, PetView, PolicyConfig
from pet_worker.scheduler import Scheduler

#: 旧版 ``agent_link.DEFAULT_EVENT_STATE_MAP`` 的逐项拷贝：等价性靠这张表钉住。
LEGACY_EVENT_STATE_MAP = {
    "SessionStart": "idle",
    "SessionEnd": "idle",
    "UserPromptSubmit": "thinking",
    "thinking": "thinking",
    "PreInvocation": "thinking",
    "PostInvocation": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Stop": "attention",
    "StopFailure": "error",
    "SubagentStop": "attention",
    "error": "error",
    "idle": "idle",
}

ACTIONS = ("rand/写代码", "rand/吃Token", "rand/轻快记录", "rand/漂浮踏步")


def append_lines(path: Path, *items: object) -> None:
    """按行追加到事件文件；字符串原样写入，其余对象序列化为 JSON。"""
    with path.open("a", encoding="utf-8", newline="") as handle:
        for item in items:
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            handle.write(text + "\n")


def ensure_events_file(path: object) -> None:
    """测试前先建出空的事件文件。

    ``start()`` 会立刻 backfill（把 offset 对齐到当前末尾），所以「文件在启动时已存在」
    是「启动之后写入的事件能被读到」的前提。文件若在启动之后才出现，首次读取仍按
    backfill 防护处理——这是旧实现就有的语义，见
    ``test_monitor_treats_file_created_later_as_history``。
    """
    if not path:
        return
    target = Path(str(path))
    if str(target).strip() in ("", "."):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)


def agent_config(path: object, *, key: str = "a1", enabled: bool = True, kind: str = "custom") -> AgentConfig:
    return AgentConfig(
        key=key,
        enabled=enabled,
        kind=kind,
        display_name="Claude",
        events_path=str(path),
    )


# ----------------------------------------------------------------------
# 无 Qt 依赖
# ----------------------------------------------------------------------
def test_package_never_imports_qt() -> None:
    """「Worker 不依赖 Qt」是硬约束：把整包导入一遍，然后确认没有 Qt 模块混进来。"""
    for module in pkgutil.walk_packages(pet_worker.__path__, prefix="pet_worker."):
        importlib.import_module(module.name)
    leaked = {name.split(".")[0] for name in sys.modules} & {"PySide6", "PyQt5", "PyQt6", "shiboken6"}
    assert leaked == set()


# ----------------------------------------------------------------------
# 事件归一化
# ----------------------------------------------------------------------
@pytest.mark.parametrize("event_name", sorted(LEGACY_EVENT_STATE_MAP))
def test_normalize_matches_legacy_map(event_name: str) -> None:
    assert normalize_event_state(event_name) == LEGACY_EVENT_STATE_MAP[event_name]


@pytest.mark.parametrize("event_name", ["", "SomeVendorEvent", "tool_result", "message"])
def test_unknown_events_are_ignored_not_working(event_name: str) -> None:
    """未知事件必须返回空串：默认当成 working 会让桌宠一直在敲键盘。"""
    assert normalize_event_state(event_name) == ""


def test_explicit_state_wins_over_event_name() -> None:
    assert normalize_event_state("Stop", "working") == "working"
    # 非法显式状态不得覆盖合法映射，也不得被当成合法值透传。
    assert normalize_event_state("Stop", "dancing") == "attention"
    assert normalize_event_state("Whatever", "dancing") == ""


def test_extract_tool_only_reads_top_level() -> None:
    assert extract_tool({"tool": " bash "}) == "bash"
    assert extract_tool({"tool": None}) == ""
    assert extract_tool({"nested": {"tool": "bash"}}) == ""
    assert extract_tool("not a dict") == ""  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Tailer
# ----------------------------------------------------------------------
def test_tailer_never_replays_history(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    append_lines(path, {"event": "Stop"})

    tailer = ByteOffsetTailer(path)
    assert tailer.read_new_lines() == []  # 首次读取只对齐 offset，历史一律不重放

    append_lines(path, {"event": "PreToolUse"})
    assert tailer.read_new_lines() == ['{"event": "PreToolUse"}']


def test_tailer_primes_so_first_events_are_not_lost(tmp_path: Path) -> None:
    """先 prime 再写入的事件必须被读到——否则「刚打开桌宠的第一件事」没反应。"""
    path = tmp_path / "events.jsonl"
    append_lines(path, {"event": "idle"})

    tailer = ByteOffsetTailer(path)
    tailer.prime()
    append_lines(path, {"event": "working"})
    assert tailer.read_new_lines() == ['{"event": "working"}']


def test_tailer_waits_for_complete_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"event":"Stop"}\n')
    tailer = ByteOffsetTailer(path)
    tailer.prime()

    with path.open("ab") as handle:
        handle.write(b'{"event":"Pre')
    assert tailer.read_new_lines() == []  # 半行不得当整行解析

    with path.open("ab") as handle:
        handle.write(b'ToolUse"}\n')
    assert tailer.read_new_lines() == ['{"event":"PreToolUse"}']


def test_tailer_resets_after_truncation(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    append_lines(path, {"event": "Stop"}, {"event": "PreToolUse"})
    tailer = ByteOffsetTailer(path)
    tailer.prime()
    append_lines(path, {"event": "idle"})
    assert tailer.read_new_lines() == ['{"event": "idle"}']

    path.write_text("", encoding="utf-8")  # 清空重写：offset 比新内容大
    append_lines(path, {"event": "error"})
    assert tailer.read_new_lines() == ['{"event": "error"}']


def test_tailer_detects_replaced_file(tmp_path: Path) -> None:
    """同路径被换成新文件时，只看 size 会永久跳过新文件前部。"""
    path = tmp_path / "events.jsonl"
    append_lines(path, {"event": "Stop"})
    tailer = ByteOffsetTailer(path)
    tailer.prime()

    path.unlink()
    append_lines(path, {"event": "PreToolUse"}, {"event": "PreToolUse"}, {"event": "idle"})
    assert len(tailer.read_new_lines()) == 3


def test_tailer_discards_overlong_line(tmp_path: Path) -> None:
    """超长行整行丢弃，它的后半截绝不能被当成一条新事件。"""
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"")
    tailer = ByteOffsetTailer(path, max_chunk_bytes=32)
    tailer.prime()

    with path.open("ab") as handle:
        handle.write(b"x" * 64 + b"\n" + b'{"event":"idle"}\n')

    # 单次读取有上界（32 字节），所以要读几次才能跨过那 64 字节的超长行；
    # 关键是整段过程里超长行的后半截一次都不能作为事件冒出来。
    collected: list[str] = []
    for _ in range(8):
        collected.extend(tailer.read_new_lines())
    assert collected == ['{"event":"idle"}']


# ----------------------------------------------------------------------
# 监视器
# ----------------------------------------------------------------------
def make_monitor(
    path: Path, *, seen: list | None = None, interval: float = 1.0, create: bool = True
) -> BaseAgentMonitor:
    if create:
        ensure_events_file(path)
    clock = FakeClock()
    scheduler = Scheduler(clock)
    collected: list = seen if seen is not None else []
    monitor = BaseAgentMonitor(
        "a1",
        ByteOffsetTailer(path),
        scheduler=scheduler,
        on_event=lambda key, state, tool: collected.append((key, state, tool)),
        poll_interval_s=interval,
    )
    monitor.test_clock = clock  # type: ignore[attr-defined]
    monitor.test_scheduler = scheduler  # type: ignore[attr-defined]
    return monitor


def test_monitor_ignores_unknown_and_broken_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    seen: list = []
    monitor = make_monitor(path, seen=seen)
    monitor.start()

    append_lines(
        path,
        "not json at all",
        [1, 2, 3],
        {"event": "某Agent私有事件"},
        {"event": "PreToolUse", "tool": "bash"},
        {"state": "thinking"},
    )
    assert monitor.poll() == 2
    assert seen == [("a1", "working", "bash"), ("a1", "thinking", "")]


def test_monitor_pause_keeps_offset(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    seen: list = []
    monitor = make_monitor(path, seen=seen)
    monitor.start()
    append_lines(path, {"event": "Stop"})
    assert monitor.poll() == 1

    monitor.pause()
    assert monitor.running is True and monitor.polling is False
    append_lines(path, {"event": "PreToolUse"})

    monitor.resume()
    assert monitor.polling is True
    assert monitor.poll() == 1  # 生产环境由调度器触发轮询，这里手动驱动一次
    assert seen[-1] == ("a1", "working", "")  # 暂停期间的写入在恢复后补读


def test_monitor_reads_events_written_right_after_start(tmp_path: Path) -> None:
    """文件在启动时已存在：启动瞬间写入的第一条事件不能被 backfill 吞掉。

    旧实现在 ``start()`` 之后、首次轮询之前有一个空窗，落在窗口里的事件会被当成历史
    丢掉；Worker 用 ``prime()`` 在 start 时就对齐到末尾，把这个窗口关掉。
    """
    path = tmp_path / "events.jsonl"
    append_lines(path, {"event": "idle"})  # 启动前的历史
    seen: list = []
    monitor = make_monitor(path, seen=seen)
    monitor.start()

    append_lines(path, {"event": "Stop"})  # 启动之后立刻写入
    assert monitor.poll() == 1
    assert seen == [("a1", "attention", "")]  # 历史那行没有被回放


def test_monitor_treats_file_created_later_as_history(tmp_path: Path) -> None:
    """启动时文件还不存在：出现后的既有内容算历史，只读它之后追加的行。

    与旧实现 ``test_missing_file_idle_then_appears`` 的语义一致：那时文件里的内容
    是别的进程在桌宠启动前写下的，重放它等于把过去的事件当成现在播。
    """
    path = tmp_path / "not_yet.jsonl"
    seen: list = []
    monitor = make_monitor(path, seen=seen, create=False)
    monitor.start()

    append_lines(path, {"event": "Stop"})
    assert monitor.poll() == 0  # 首次发现文件：只 backfill，不回放

    append_lines(path, {"event": "PreToolUse"})
    assert monitor.poll() == 1
    assert seen == [("a1", "working", "")]


def test_monitor_polls_on_schedule(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    seen: list = []
    monitor = make_monitor(path, seen=seen, interval=1.5)
    monitor.start()
    append_lines(path, {"event": "Stop"})

    scheduler: Scheduler = monitor.test_scheduler  # type: ignore[attr-defined]
    clock: FakeClock = monitor.test_clock  # type: ignore[attr-defined]
    scheduler.tick()
    assert seen == []  # 未到期不轮询

    clock.advance(1.5)
    scheduler.tick()
    assert seen == [("a1", "attention", "")]

    monitor.stop()
    assert scheduler.pending == 0


def test_custom_agent_never_creates_directories(tmp_path: Path) -> None:
    """事件文件路径来自用户配置，可能是只读挂载：替用户 mkdir 是越界行为。"""
    target = tmp_path / "不存在的目录" / "events.jsonl"
    scheduler = Scheduler(FakeClock())
    monitor = CustomAgentMonitor(
        "a1", target, scheduler=scheduler, on_event=lambda *args: None, poll_interval_s=1.0
    )
    monitor.start()
    assert monitor.poll() == 0
    assert not target.parent.exists()


def test_custom_agent_expands_home(tmp_path: Path) -> None:
    monitor = CustomAgentMonitor(
        "a1", "~/events.jsonl", scheduler=Scheduler(FakeClock()), on_event=lambda *args: None
    )
    assert monitor.events_file == Path.home() / "events.jsonl"


# ----------------------------------------------------------------------
# 装配层
# ----------------------------------------------------------------------
class StubRuntime:
    """只记录协议调用，不真的写流。"""

    def __init__(self, pet: PetView) -> None:
        self.pet = pet
        self.states: list[tuple[str, str, str]] = []
        self.proposals: list[tuple[str, str | None, dict | None]] = []

    def emit_state_changed(self, agent_key: str, state: str, tool: str = "") -> bool:
        self.states.append((agent_key, state, tool))
        return True

    def emit_behavior_propose(
        self,
        action_id: str,
        *,
        agent: str | None = None,
        bubble: dict | None = None,
        ttl_ms: int = 2000,
    ) -> str:
        self.proposals.append((action_id, agent, bubble))
        return f"prop-{len(self.proposals)}"


def make_manager(
    *,
    path: object,
    backend: str = "python",
    visible: bool = True,
    enabled: bool = True,
    kind: str = "custom",
    agents: dict[str, AgentConfig] | None = None,
) -> tuple[AgentManager, StubRuntime, FakeClock, Scheduler]:
    ensure_events_file(path)
    clock = FakeClock()
    scheduler = Scheduler(clock)
    runtime = StubRuntime(PetView(generation=3, character="random", visible=visible, actions=ACTIONS))
    manager = AgentManager(runtime, scheduler=scheduler, clock=clock, poll_interval_s=1.0)
    if agents is None:
        agents = {"a1": agent_config(path, enabled=enabled, kind=kind)}
    manager.apply_config(
        ConfigView(revision=1, agent_backend=backend, agents=agents, policy=PolicyConfig())
    )
    manager.apply_pet(runtime.pet)
    return manager, runtime, clock, scheduler


def test_manager_full_chain(tmp_path: Path) -> None:
    """真实 JSONL → 状态事件 + 行为建议。"""
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path)
    assert manager.health()["agents"]["a1"]["running"] is True

    append_lines(path, {"event": "PreToolUse", "tool": "bash"})
    clock.advance(1.0)
    scheduler.tick()

    assert runtime.states == [("a1", "working", "bash")]
    assert runtime.proposals
    action_id, agent, _ = runtime.proposals[0]
    assert action_id in ACTIONS and agent == "a1"


def test_manager_waits_for_complete_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path)

    with path.open("ab") as handle:
        handle.write(b'{"event":"PreToolUse"')
    clock.advance(1.0)
    scheduler.tick()
    assert runtime.states == []

    with path.open("ab") as handle:
        handle.write(b"}\n")
    clock.advance(1.0)
    scheduler.tick()
    assert runtime.states == [("a1", "working", "")]


def test_manager_ignores_unknown_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path)
    append_lines(path, {"event": "某私有事件"}, {"event": "AnotherVendorThing"})
    clock.advance(1.0)
    scheduler.tick()
    assert runtime.states == []
    assert runtime.proposals == []


def test_manager_stays_idle_for_native_backend(tmp_path: Path) -> None:
    """后端不是 python 时绝不读文件：两套来源同时驱动会让动作互相打架。"""
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path, backend="native")
    assert manager.health()["driving"] is False
    assert manager.health()["agents"]["a1"]["running"] is False

    append_lines(path, {"event": "Stop"})
    clock.advance(5.0)
    scheduler.tick()
    assert runtime.states == []


def test_manager_switches_backend_both_ways(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path)

    manager.apply_config(
        ConfigView(revision=2, agent_backend="native", agents={"a1": agent_config(path)}, policy=PolicyConfig())
    )
    assert manager.health()["agents"]["a1"]["polling"] is False
    append_lines(path, {"event": "Stop"})
    clock.advance(2.0)
    scheduler.tick()
    assert runtime.states == []

    manager.apply_config(
        ConfigView(revision=3, agent_backend="python", agents={"a1": agent_config(path)}, policy=PolicyConfig())
    )
    assert manager.health()["agents"]["a1"]["polling"] is True
    clock.advance(1.0)
    scheduler.tick()
    # 暂停期间的事件仍然补读，没有丢
    assert runtime.states == [("a1", "attention", "")]


def test_manager_skips_disabled_and_unsupported_agents(tmp_path: Path) -> None:
    manager, runtime, _, scheduler = make_manager(
        path=tmp_path / "e.jsonl", agents={"a1": agent_config(tmp_path / "e.jsonl", enabled=False)}
    )
    assert manager.health()["agents"] == {}

    manager, runtime, _, scheduler = make_manager(
        path=tmp_path / "e.jsonl", agents={"a1": agent_config(tmp_path / "e.jsonl", kind="http")}
    )
    assert manager.health()["agents"] == {}

    manager, runtime, _, scheduler = make_manager(
        path="", agents={"a1": AgentConfig(key="a1", enabled=True, kind="custom", events_path="")}
    )
    assert manager.health()["agents"] == {}


def pet_event(manager: AgentManager, runtime: StubRuntime, name: str) -> None:
    """复刻真实 ``Runtime._on_pet_event``：先按事件维护可见性/交互锁，再回调装配层。

    管理者是只读 ``runtime.pet`` 判断要不要轮询的，宿主不会替它推断可见性。
    """
    if name == "hidden":
        runtime.pet.visible = False
    elif name == "shown":
        runtime.pet.visible = True
    elif name == "drag_start":
        runtime.pet.interaction_locked = True
    elif name == "drag_end":
        runtime.pet.interaction_locked = False
    manager.on_pet_event(name, {})


def test_manager_pauses_when_hidden(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path)

    pet_event(manager, runtime, "hidden")
    assert manager.health()["agents"]["a1"]["polling"] is False

    pet_event(manager, runtime, "shown")
    assert manager.health()["agents"]["a1"]["polling"] is True

    pet_event(manager, runtime, "drag_start")  # 拖拽只锁交互，不该停止读文件
    assert manager.health()["agents"]["a1"]["polling"] is True


def test_manager_rebuilds_monitor_on_path_change(tmp_path: Path) -> None:
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=first)

    ensure_events_file(second)  # 换路径前先让新文件存在，重建时的 backfill 才有末梢可对齐
    manager.apply_config(
        ConfigView(revision=2, agent_backend="python", agents={"a1": agent_config(second)}, policy=PolicyConfig())
    )
    assert manager.health()["agents"]["a1"]["path"] == str(second)

    append_lines(second, {"event": "Stop"})
    clock.advance(1.0)
    scheduler.tick()
    assert runtime.states == [("a1", "attention", "")]


def test_manager_stop_is_final(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    manager, runtime, clock, scheduler = make_manager(path=path)
    manager.stop()
    assert manager.health()["agents"] == {}

    append_lines(path, {"event": "Stop"})
    clock.advance(5.0)
    scheduler.tick()
    manager.apply_pet(PetView(generation=4, visible=True, actions=ACTIONS))
    manager.on_pet_event("hidden", {})
    assert runtime.states == []
