# -*- coding: utf-8 -*-
"""故障与背压：stdin EOF、启动路径、非法 JSON 洪峰、超限中止、优雅关闭。

Worker 崩溃/卡死的恢复由宿主 Supervisor 负责（C++ 侧），这里验证 Worker 本身
在每个故障场景下的退出码与存活语义，与 __main__.py 文档的退出码表一一对应。
"""

from __future__ import annotations

import subprocess
import time

from host_harness import WorkerHost


def make_config(path) -> dict:
    return {
        "revision": 1,
        "agent_backend": "python",
        "agents": {
            "a1": {
                "enabled": True,
                "kind": "custom",
                "display_name": "故障测试",
                "events_path": str(path),
                "thinking_text": "",
            }
        },
    }


def make_pet() -> dict:
    return {
        "generation": 1,
        "character": "random",
        "visible": True,
        "interaction_locked": False,
        "actions": ["random/写代码"],
    }


def test_stdin_eof_after_handshake_exits_zero(tmp_path) -> None:
    """宿主没了（EOF）：已完成握手的 Worker 正常退出，退出码 0。"""
    worker = WorkerHost()
    try:
        (tmp_path / "events.jsonl").write_bytes(b"")
        worker.prepare(make_config(tmp_path / "events.jsonl"), make_pet())
        assert worker.proc.stdin is not None
        worker.proc.stdin.close()
        assert worker.proc.wait(timeout=10) == 0
    finally:
        worker.close(graceful=False)


def test_stdin_eof_without_handshake_exits_three(tmp_path) -> None:
    """EOF 且始终没握手：退出码 3（EXIT_NO_HANDSHAKE）。"""
    worker = WorkerHost()
    try:
        assert worker.proc.stdin is not None
        worker.proc.stdin.close()
        assert worker.proc.wait(timeout=10) == 3
    finally:
        worker.close(graceful=False)


def test_invalid_json_flood_does_not_break_channel(tmp_path) -> None:
    """非法 JSON 洪峰：逐条隔离，后续合法消息照常处理。"""
    worker = WorkerHost()
    try:
        (tmp_path / "events.jsonl").write_bytes(b"")
        worker.prepare(make_config(tmp_path / "events.jsonl"), make_pet())
        for _ in range(200):
            worker.send_bytes(b"{not json\n")
        ping = worker.send("runtime.ping", kind="request", request_id="after-flood")
        worker.wait_for(lambda m: m.get("request_id") == "after-flood" and m.get("type") == "runtime.pong", timeout=10)
    finally:
        worker.close()


def test_oversize_unterminated_line_aborts_session(tmp_path) -> None:
    """缓冲无限累积的唯一出口：立即中止会话，退出码 2（EXIT_PROTOCOL_ABORT）。

    会话中止后 Worker 不自行退出（宿主会杀进程并重启），退出发生在 stdin EOF 之后。
    """
    worker = WorkerHost()
    try:
        worker.prepare(make_config(tmp_path / "events.jsonl"), make_pet())
        worker.send_bytes(b"x" * 70000)
        # 中止后的会话对合法消息也不再有反应
        time.sleep(0.5)
        worker.send("runtime.ping", kind="request", request_id="post-abort")
        time.sleep(1.0)
        assert not [m for m in worker.messages if m.get("request_id") == "post-abort"]
        # 宿主随即关闭 stdin：Worker 走退出路径，退出码 2
        assert worker.proc.stdin is not None
        worker.proc.stdin.close()
        assert worker.proc.wait(timeout=15) == 2
    finally:
        worker.close(graceful=False)


def test_shutdown_event_exits_zero(tmp_path) -> None:
    """runtime.shutdown：Worker 主动退出，退出码 0，无需宿主强杀。"""
    worker = WorkerHost()
    try:
        worker.prepare(make_config(tmp_path / "events.jsonl"), make_pet())
        worker.send("runtime.shutdown")
        assert worker.proc.wait(timeout=10) == 0
    finally:
        worker.close(graceful=False)


def test_wrong_kind_snapshot_is_rejected_but_session_survives(tmp_path) -> None:
    """kind 耦合错误（snapshot 以 request 发出）：整条拒绝，会话不中止。"""
    worker = WorkerHost()
    try:
        worker.prepare(make_config(tmp_path / "events.jsonl"), make_pet())
        (tmp_path / "events.jsonl").write_bytes(b"")
        # 正确 kind 是 event；这里故意用 request 触发 invalid_envelope
        worker.send("config.snapshot", make_config(tmp_path / "events.jsonl"), kind="request", request_id="bad-1")
        time.sleep(1.0)
        ping = worker.send("runtime.ping", kind="request", request_id="still-alive")
        worker.wait_for(lambda m: m.get("request_id") == "still-alive", timeout=10)
    finally:
        worker.close()


def test_worker_ignores_garbage_then_processes_events(tmp_path) -> None:
    """stdout 纯净性的对偶：向 Worker 混合投喂空行/CRLF/坏行后业务仍正常。"""
    worker = WorkerHost()
    try:
        events = tmp_path / "events.jsonl"
        events.write_bytes(b"")
        worker.prepare(make_config(events), make_pet())
        worker.send_bytes(b"\n\r\n\n")
        for _ in range(20):
            worker.send_bytes(b"garbage-without-newline-and-also-too-long")
        worker.send_bytes(b'{"broken":\n')

        from json import dumps
        with events.open("ab") as handle:
            handle.write((dumps({"event": "PreToolUse", "tool": "bash"}, ensure_ascii=False) + "\n").encode("utf-8"))
        state = worker.wait_for(
            lambda m: m.get("type") == "agent.state_changed" and m.get("payload", {}).get("state") == "working",
            timeout=15,
        )
        assert state["payload"]["tool"] == "bash"
    finally:
        worker.close()
