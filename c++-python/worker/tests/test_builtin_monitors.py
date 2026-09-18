# -*- coding: utf-8 -*-
"""内建监视器（chatgpt / antigravity）+ Codex 状态读取器 + antigravity 协议解析。

移植等价性防线：每个用例都对应 C++ 实现里一条真实分支，用假时钟 / 临时目录 /
内存 sqlite 验证，不依赖网络与真实 Codex。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from _support import FakeClock

from pet_worker.agents.antigravity import AntigravityMonitor
from pet_worker.agents.chatgpt import ChatGptMonitor
from pet_worker.agents.codex_status import CodexSnapshot, CodexStatusReader
from pet_worker.agents.manager import AgentManager
from pet_worker.agents.protocol import antigravity_event_state, antigravity_event_tool
from pet_worker.agents.tailer import ByteOffsetTailer
from pet_worker.runtime import AgentConfig, ConfigView, PetView, PolicyConfig
from pet_worker.scheduler import Scheduler


def append_lines(path: Path, *items: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        for item in items:
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            handle.write(text + "\n")


# ----------------------------------------------------------------------
# protocol：antigravity_event_state / antigravity_event_tool
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"state": "thinking"}, "thinking"),
        ({"state": "dancing"}, ""),
        ({"type": "user_input"}, "thinking"),
        ({"event": "PreInvocation"}, "thinking"),
        ({"role": "user"}, "thinking"),
        ({"source": "user_explicit"}, "thinking"),
        ({"type": "PreToolUse"}, "working"),
        ({"event": "PostToolUse"}, "working"),
        ({"type": "PostInvocation"}, "working"),
        ({"type": "Stop"}, "idle"),
        ({"type": "SessionEnd"}, "idle"),
        ({"type": "whatever", "tool_calls": [{"name": "bash"}]}, "working"),
        ({"type": "planner_response"}, "working"),
        ({"type": "planner_response", "status": "done"}, "idle"),
        ({"type": "model_response", "status": "finished"}, "idle"),
        ({"type": "step_start"}, "working"),
        ({"status": "success"}, "idle"),
        ({"status": "error"}, "error"),
        ({"status": "failed"}, "error"),
        ({"type": "unknown_gibberish"}, ""),
        ({"role": "assistant", "status": "pending"}, ""),
        ({"type": "user_input", "status": "done"}, "thinking"),
    ],
)
def test_antigravity_event_state_branches(payload: dict, expected: str) -> None:
    assert antigravity_event_state(payload) == expected


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"tool": " bash "}, "bash"),
        ({"tool": None}, ""),
        ({"tool_calls": [{"function": {"name": " grep_search "}}]}, "grep_search"),
        ({"tool_calls": [{"name": "run_command"}]}, "run_command"),
        ({"tool_calls": [{"tool_name": "view_file"}]}, "view_file"),
        ({"tool_calls": [{"toolAction": "browser_subagent"}]}, "browser_subagent"),
        ({"tool_calls": [{"toolSummary": "read_url_content"}]}, "read_url_content"),
        ({"tool_calls": []}, ""),
        ({"tool_calls": "nope"}, ""),
        ({"tool_calls": [42]}, ""),
        ({}, ""),
        ("not a dict", ""),
    ],
)
def test_antigravity_event_tool_branches(payload: object, expected: str) -> None:
    assert antigravity_event_tool(payload) == expected  # type: ignore[arg-type]
# <<CONT>>

# ----------------------------------------------------------------------
# codex_status
# ----------------------------------------------------------------------
def make_codex_home(tmp_path: Path, name: str = "codex") -> tuple[Path, Path, Path]:
    """造最小 state_N / thread_history_N sqlite 对。"""
    home = tmp_path / name
    home.mkdir()
    conn = sqlite3.connect(home / "state_1.sqlite")
    conn.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, "
        "archived INTEGER DEFAULT 0, updated_at INTEGER)")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(home / "thread_history_1.sqlite")
    conn.execute(
        "CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, "
        "started_at INTEGER, rollout_ordinal INTEGER)")
    conn.execute(
        "CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, "
        "item_json TEXT, rollout_ordinal INTEGER)")
    conn.commit()
    conn.close()
    return home, home / "state_1.sqlite", home / "thread_history_1.sqlite"


def insert_thread(state: Path, thread_id: str = "t1", rollout_path: str = "",
                  archived: int = 0, updated_at: int = 100) -> None:
    conn = sqlite3.connect(state)
    conn.execute("INSERT INTO threads (id,rollout_path,archived,updated_at) VALUES (?,?,?,?)",
                 (thread_id, rollout_path, archived, updated_at))
    conn.commit()
    conn.close()


def insert_turn(history: Path, thread_id: str = "t1", turn_id: str = "u1",
                status: str = "inProgress", started_at: int = 100) -> None:
    conn = sqlite3.connect(history)
    conn.execute("INSERT INTO thread_turns VALUES (?,?,?,?,1)",
                 (thread_id, turn_id, status, started_at))
    conn.commit()
    conn.close()


def insert_item(history: Path, item_json: dict, thread_id: str = "t1",
                turn_id: str = "u1", item_id: str = "i1") -> None:
    conn = sqlite3.connect(history)
    conn.execute("INSERT INTO thread_items VALUES (?,?,?,?,1)",
                 (thread_id, turn_id, item_id, json.dumps(item_json)))
    conn.commit()
    conn.close()


def test_codex_unavailable_without_databases(tmp_path: Path) -> None:
    reader = CodexStatusReader(str(tmp_path / "empty"))
    snap = reader.read()
    assert snap.state == "unavailable"
    assert snap.detail == "未检测到可读取的本机 Codex 任务"


def test_codex_unreadable_db_folds_to_unreadable(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    home.mkdir()
    (home / "state_1.sqlite").write_bytes(b"not a database at all")
    (home / "thread_history_1.sqlite").write_bytes(b"not a database at all")
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail) == (
        "unavailable", "任务记录暂不可读或版本不兼容；稍后自动重试")


def test_codex_no_turns(tmp_path: Path) -> None:
    home, state, _ = make_codex_home(tmp_path)
    insert_thread(state)
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail) == ("unavailable", "尚无可监听的任务回合")
# <<CONT>>

@pytest.mark.parametrize("status", ["inProgress", "in_progress", "running"])
def test_codex_running_without_item_is_thinking(tmp_path: Path, status: str) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history, status=status)
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail, snap.thread_id, snap.turn_id) == (
        "thinking", "正在思考", "t1", "u1")
    assert snap.tool == "" and snap.item_id == ""


def test_codex_known_kind_is_working(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history)
    insert_item(history, {"type": "commandExecution", "status": "inProgress"})
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail, snap.tool, snap.item_id) == (
        "working", "正在执行任务", "shell", "i1")


def test_codex_unknown_kind_is_thinking(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history)
    insert_item(history, {"type": "gibberish_kind", "status": "inProgress"})
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail, snap.tool) == ("thinking", "正在思考或生成回复", "")


def test_codex_explicit_tool_wins_and_truncated(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history)
    insert_item(history, {"type": "mcpToolCall", "status": "inProgress", "tool": "x" * 150})
    snap = CodexStatusReader(str(home)).read()
    assert snap.tool == "x" * 100


def test_codex_terminal_statuses(tmp_path: Path) -> None:
    cases = {
        "completed": ("idle", "任务完成"),
        "interrupted": ("interrupted", "任务已中断"),
        "failed": ("error", "任务失败"),
        "error": ("error", "任务失败"),
    }
    for i, (status, (want_state, want_detail)) in enumerate(cases.items()):
        home, state, history = make_codex_home(tmp_path, name=f"codex{i}")
        insert_thread(state)
        insert_turn(history, status=status, started_at=7)
        snap = CodexStatusReader(str(home)).read()
        assert (snap.state, snap.detail, snap.started_at) == (want_state, want_detail, 7)


def test_codex_unsupported_turn_status(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history, status="weird")
    assert CodexStatusReader(str(home)).read().state == "unavailable"


@pytest.mark.parametrize(
    "status",
    ["waitingForApproval", "pendingApproval", "waiting_for_approval", "waitingForInput"],
)
def test_codex_approval_states_need_attention(tmp_path: Path, status: str) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history)
    insert_item(history, {"type": "commandExecution", "status": status})
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail) == ("attention", "需要你确认或回答")


def test_codex_questions_need_attention(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history)
    insert_item(history, {"type": "gibberish_kind", "status": "inProgress",
                          "questions": [{"q": 1}]})
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail) == ("attention", "需要你确认或回答")


def test_codex_request_user_input_while_running(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state)
    insert_turn(history)
    insert_item(history, {"type": "mcpToolCall", "status": "inProgress",
                          "tool": "request_user_input"})
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.detail) == ("attention", "正在等你回答")


def test_codex_archived_and_agent_path_filters(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state, thread_id="gone", archived=1)       # 归档：排除
    insert_thread(state, thread_id="other", updated_at=300)  # agent_path 非空：排除
    conn = sqlite3.connect(state)
    conn.execute("ALTER TABLE threads ADD COLUMN agent_path TEXT")
    conn.execute("UPDATE threads SET agent_path='/somewhere' WHERE id='other'")
    conn.commit()
    conn.close()
    insert_thread(state, thread_id="root", updated_at=200)   # agent_path='/root'：保留
    insert_turn(history, thread_id="root")
    snap = CodexStatusReader(str(home)).read()
    assert (snap.state, snap.thread_id) == ("thinking", "root")


def test_codex_history_id_maps_rollout_suffix(tmp_path: Path) -> None:
    """rollout 文件名末段作为 thread_history 的键，但对外仍是 public id。"""
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state, thread_id="pub1", rollout_path="rollout_2025_ab12cd.jsonl")
    insert_turn(history, thread_id="ab12cd")
    snap = CodexStatusReader(str(home)).read()
    assert snap.thread_id == "pub1"
# <<CONT>>

def test_codex_database_path_picks_largest_suffix(tmp_path: Path) -> None:
    home, _, history = make_codex_home(tmp_path)
    for suffix, thread_id in ((2, "old2"), (5, "mid5")):
        conn = sqlite3.connect(home / f"state_{suffix}.sqlite")
        conn.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, "
            "archived INTEGER DEFAULT 0, updated_at INTEGER)")
        conn.execute("INSERT INTO threads (id,archived,updated_at) VALUES (?,0,100)", (thread_id,))
        conn.commit()
        conn.close()
    (home / "state_abc.sqlite").write_bytes(b"junk")  # 非数字后缀：跳过
    conn = sqlite3.connect(home / "state_12.sqlite")
    conn.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, "
        "archived INTEGER DEFAULT 0, updated_at INTEGER)")
    conn.execute("INSERT INTO threads VALUES ('new12','',0,900)")
    conn.commit()
    conn.close()
    insert_turn(history, thread_id="new12")

    reader = CodexStatusReader(str(home))
    assert reader.database_path("state") == str(home / "state_12.sqlite")
    assert reader.read().thread_id == "new12"


def test_codex_fixed_thread_lookup(tmp_path: Path) -> None:
    home, state, history = make_codex_home(tmp_path)
    insert_thread(state, thread_id="t1")
    insert_thread(state, thread_id="t2", updated_at=200)
    insert_turn(history, thread_id="t2", turn_id="u2")
    snap = CodexStatusReader(str(home)).read("t2")
    assert (snap.thread_id, snap.turn_id) == ("t2", "u2")
# <<CONT>>

# ----------------------------------------------------------------------
# antigravity
# ----------------------------------------------------------------------
def make_antigravity(tmp_path: Path, *, base_dir: Path | None = None,
                     hooks_path: Path | None = None):
    events = tmp_path / "events" / "antigravity.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.touch()
    clock = FakeClock()
    scheduler = Scheduler(clock)
    seen: list = []
    monitor = AntigravityMonitor(
        "antigravity",
        str(events),
        scheduler=scheduler,
        on_event=lambda key, state, tool: seen.append((key, state, tool)),
        base_dir=base_dir,
        hooks_path=hooks_path,
    )
    return monitor, seen, clock, scheduler, events


def test_antigravity_start_installs_hooks(tmp_path: Path) -> None:
    hooks = tmp_path / "cfg" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({"desktop-pet-hook-legacy": {"old": True}}), encoding="utf-8")

    monitor, seen, clock, scheduler, events = make_antigravity(tmp_path, hooks_path=hooks)
    monitor.start()
    monitor.stop()

    script = events.parent / "antigravity_event_hook.ps1"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "ConvertFrom-Json" in text and "antigravity.jsonl" in text

    data = json.loads(hooks.read_text(encoding="utf-8"))
    assert "desktop-pet-hook-legacy" not in data  # 旧键被移除
    hook = data["desktop-pet-hook"]
    assert hook["x-desktop-pet"] is True
    for event in ("PreInvocation", "PreToolUse", "PostToolUse", "Stop"):
        assert hook[event], event
    assert "powershell -NoProfile -ExecutionPolicy Bypass -File" in (
        hook["PreInvocation"][0]["command"])
    assert hook["PreToolUse"][0]["matcher"] == "*"


def test_antigravity_uninstall_hooks(tmp_path: Path) -> None:
    hooks = tmp_path / "cfg" / "hooks.json"
    monitor, *_ = make_antigravity(tmp_path, hooks_path=hooks)
    assert monitor.install_hooks(str(tmp_path / "events" / "antigravity.jsonl")) is True
    assert "desktop-pet-hook" in json.loads(hooks.read_text(encoding="utf-8"))
    assert monitor.uninstall_hooks() is True
    data = json.loads(hooks.read_text(encoding="utf-8"))
    assert "desktop-pet-hook" not in data and "desktop-pet-hook-legacy" not in data
    assert monitor.uninstall_hooks() is True  # 幂等


def test_antigravity_events_file_channel(tmp_path: Path) -> None:
    monitor, seen, clock, scheduler, events = make_antigravity(
        tmp_path, base_dir=tmp_path / "brain", hooks_path=tmp_path / "hooks.json")
    monitor.start()
    append_lines(
        events,
        {"ts": 1.0, "agent": "antigravity", "state": "thinking", "event": "PreInvocation"},
        {"ts": 2.0, "agent": "antigravity", "state": "working", "event": "PreToolUse",
         "tool": "bash"},
        {"ts": 3.0, "agent": "antigravity", "state": "idle", "event": "Stop"},
    )
    assert monitor.poll() == 3
    assert seen == [
        ("antigravity", "thinking", ""),
        ("antigravity", "working", "bash"),
        ("antigravity", "idle", ""),
    ]


def test_antigravity_transcript_tailer_channel(tmp_path: Path) -> None:
    base = tmp_path / "brain"
    conv = base / "session-1"
    conv.mkdir(parents=True)
    transcript = conv / "transcript.jsonl"
    transcript.write_text('{"type":"PreInvocation"}\n', encoding="utf-8")  # 历史

    monitor, seen, clock, scheduler, events = make_antigravity(
        tmp_path, base_dir=base, hooks_path=tmp_path / "hooks.json")
    monitor.start()
    assert monitor.poll() == 0  # 首次扫描建立 tailer，既有内容是历史

    append_lines(
        transcript,
        {"type": "PreToolUse", "tool_calls": [{"name": "grep_search"}]},
        {"role": "user"},
        {"type": "Stop"},
        {"type": "unknown_gibberish"},  # 不认识的行被忽略
    )
    assert monitor.poll() == 3
    assert seen == [
        ("antigravity", "working", "grep_search"),
        ("antigravity", "thinking", ""),
        ("antigravity", "idle", ""),
    ]
# <<CONT>>

def make_conversation_db(conv_dir: Path, name: str = "a.db") -> Path:
    conv_dir.mkdir(parents=True, exist_ok=True)
    db = conv_dir / name
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, "
        "status INTEGER, metadata BLOB)")
    conn.commit()
    conn.close()
    return db


def test_antigravity_db_channel_running_to_idle(tmp_path: Path) -> None:
    base = tmp_path / "brain"
    base.mkdir()
    db = make_conversation_db(tmp_path / "conversations")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO steps VALUES (1, 5, 2, ?)", (b"\x12\x0brun_command",))
    conn.commit()
    conn.close()

    monitor, seen, clock, scheduler, events = make_antigravity(
        tmp_path, base_dir=base, hooks_path=tmp_path / "hooks.json")
    monitor.start()
    assert monitor.poll() == 1
    assert seen == [("antigravity", "working", "run_command")]

    # planner_response（step_type 15）→ thinking
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO steps VALUES (2, 15, 2, X'')")
    conn.commit()
    conn.close()
    assert monitor.poll() == 1
    assert seen[-1] == ("antigravity", "thinking", "")

    # status 3 → idle（busy 转出）
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO steps VALUES (3, 5, 3, X'')")
    conn.commit()
    conn.close()
    assert monitor.poll() == 1
    assert seen[-1] == ("antigravity", "idle", "")

    # 活动库 mtime 是真实墙钟：用真实时间推进 700s 验证 600s 窗口
    import time as _time
    monitor._clock = _time.time
    stale = _time.time() - 700
    os.utime(db, (stale, stale))
    assert monitor.poll() == 0
    assert len(seen) == 3


def test_antigravity_db_recency_expiry_emits_idle(tmp_path: Path) -> None:
    """活动库整体过期时，从 busy 折回 idle。"""
    base = tmp_path / "brain"
    base.mkdir()
    db = make_conversation_db(tmp_path / "conversations")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO steps VALUES (1, 5, 2, ?)", (b"\x12\x0brun_command",))
    conn.commit()
    conn.close()

    monitor, seen, clock, scheduler, events = make_antigravity(
        tmp_path, base_dir=base, hooks_path=tmp_path / "hooks.json")
    monitor.start()
    assert monitor.poll() == 1
    assert seen == [("antigravity", "working", "run_command")]

    # 活动库 mtime 是真实墙钟：用真实时间推进 700s 验证 600s 窗口过期时从 busy 折回 idle
    import time as _time
    monitor._clock = _time.time
    stale = _time.time() - 700
    os.utime(db, (stale, stale))
    assert monitor.poll() == 1
    assert seen[-1] == ("antigravity", "idle", "")


def test_antigravity_extract_tool_from_metadata() -> None:
    extract = AntigravityMonitor.extract_tool_from_metadata
    assert extract(b"") == ""
    assert extract("") == ""
    assert extract(b"\x12\x0brun_command") == "run_command"  # known 列表优先
    assert extract(b"prefix \x12\x05hello tail") == "hello"  # 正则兜底
    assert extract(b"\x12\x03ab") == ""                       # 名字太短不匹配
    assert extract(b"nothing here") == ""
# <<CONT>>

# ----------------------------------------------------------------------
# chatgpt
# ----------------------------------------------------------------------
class FakeReader:
    """按序吐出预设快照；耗尽后一直返回最后一个（轮询会持续发生）。"""

    def __init__(self, *snapshots: CodexSnapshot) -> None:
        self.snapshots = list(snapshots) or [CodexSnapshot()]

    def read(self, thread_id: str = "") -> CodexSnapshot:
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


def make_chatgpt(snapshots: list[CodexSnapshot]):
    clock = FakeClock()
    scheduler = Scheduler(clock)
    seen: list = []
    monitor = ChatGptMonitor(
        "chatgpt",
        scheduler=scheduler,
        on_event=lambda key, state, tool: seen.append((key, state, tool)),
        reader=FakeReader(*snapshots),
    )
    return monitor, seen, clock, scheduler


def test_chatgpt_snapshot_diff_suppression(tmp_path: Path) -> None:
    """基线 idle 不发、task 变化的 idle 不发、busy 发、同回合完成发。"""
    idle_t1 = CodexSnapshot(state="idle", thread_id="t1", turn_id="u1", detail="任务完成")
    busy = CodexSnapshot(state="thinking", thread_id="t1", turn_id="u1",
                         started_at=10, detail="正在思考")
    done_t1 = CodexSnapshot(state="idle", thread_id="t1", turn_id="u1", detail="任务完成")
    idle_t2 = CodexSnapshot(state="idle", thread_id="t2", turn_id="u9", detail="任务完成")

    monitor, seen, clock, scheduler = make_chatgpt([idle_t1, busy, done_t1, idle_t2])
    monitor.start()

    scheduler.tick()  # start 安排的立即一拍
    assert seen == []  # 基线已完成任务：不庆祝旧历史

    clock.advance(1.0)
    scheduler.tick()
    assert seen == [("chatgpt", "thinking", "")]  # busy 发状态
    assert monitor._task is not None
    assert abs(monitor._task.deadline - clock()) == pytest.approx(0.25)  # busy → 250ms

    clock.advance(0.25)
    scheduler.tick()
    assert seen == [("chatgpt", "thinking", ""), ("chatgpt", "idle", "")]  # 同回合完成
    assert abs(monitor._task.deadline - clock()) == pytest.approx(1.0)  # idle → 1000ms

    clock.advance(1.0)
    scheduler.tick()
    assert len(seen) == 2  # 快照不变：不发

    clock.advance(1.0)
    scheduler.tick()
    assert len(seen) == 2  # 新任务（t2）的首个 idle：task 变化 → 抑制


def test_chatgpt_tool_activity_attach(tmp_path: Path) -> None:
    s1 = CodexSnapshot(state="working", thread_id="t", turn_id="u", item_id="i1", tool="shell")
    s2 = CodexSnapshot(state="working", thread_id="t", turn_id="u", item_id="i1", tool="shell")
    s3 = CodexSnapshot(state="working", thread_id="t", turn_id="u", item_id="i2", tool="edit")

    monitor, seen, clock, scheduler = make_chatgpt([s1, s2, s3])
    monitor.start()
    scheduler.tick()
    assert seen == [("chatgpt", "working", "shell")]  # tool 非空且首次出现

    clock.advance(0.25)
    scheduler.tick()
    assert len(seen) == 1  # 快照完全不变 → 不发

    clock.advance(0.25)
    scheduler.tick()
    assert seen[-1] == ("chatgpt", "working", "edit")  # item/tool 变化 → 再次附带


def test_chatgpt_unavailable_state_is_published() -> None:
    snap = CodexSnapshot(detail="任务记录暂不可读或版本不兼容；稍后自动重试")
    monitor, seen, clock, scheduler = make_chatgpt([snap])
    monitor.start()
    scheduler.tick()
    assert seen == [("chatgpt", "unavailable", "")]


def test_chatgpt_lifecycle() -> None:
    monitor, seen, clock, scheduler = make_chatgpt([CodexSnapshot()])
    assert monitor.running is False and monitor.polling is False

    monitor.start()
    assert monitor.running is True and monitor.polling is True

    monitor.pause()
    assert monitor.running is True and monitor.polling is False
    clock.advance(5.0)
    scheduler.tick()
    assert seen == []  # 暂停期间不轮询

    monitor.resume()
    assert monitor.polling is True
    monitor.stop()
    assert monitor.running is False
    assert scheduler.pending == 0
    assert monitor.snapshot == CodexSnapshot()  # stop 重置快照


# ----------------------------------------------------------------------
# 装配层：内建 kind 的构建
# ----------------------------------------------------------------------
class _StubRuntime:
    def __init__(self) -> None:
        self.pet = PetView(generation=1, visible=False, actions=())

    def emit_state_changed(self, *args: object, **kwargs: object) -> bool:
        return True

    def emit_behavior_propose(self, *args: object, **kwargs: object) -> str:
        return "prop"


def test_manager_constructs_builtin_kinds(tmp_path: Path) -> None:
    """宿主不可见时只构建不启动：antigravity 不会真的把 hook 写进用户主目录。"""
    events = tmp_path / "antigravity.jsonl"
    events.touch()
    scheduler = Scheduler(FakeClock())
    manager = AgentManager(_StubRuntime(), scheduler=scheduler, clock=FakeClock())
    manager.apply_config(
        ConfigView(
            revision=1,
            agent_backend="python",
            agents={
                "ag": AgentConfig(key="ag", enabled=True, kind="antigravity",
                                  events_path=str(events)),
                "cg": AgentConfig(key="cg", enabled=True, kind="chatgpt", events_path=""),
            },
            policy=PolicyConfig(),
        )
    )
    health = manager.health()
    assert set(health["agents"]) == {"ag", "cg"}
    from pet_worker.agents.antigravity import AntigravityMonitor as AM
    from pet_worker.agents.chatgpt import ChatGptMonitor as CM
    assert isinstance(manager._monitors["ag"], AM)
    assert isinstance(manager._monitors["cg"], CM)
    assert all(item["running"] is False for item in health["agents"].values())
    assert health["agents"]["cg"]["path"] == ""  # chatgpt 无需事件路径