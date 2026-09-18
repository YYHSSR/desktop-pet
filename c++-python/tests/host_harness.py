# -*- coding: utf-8 -*-
"""最小宿主：以 stdio 启动真实 Worker 进程，供集成测试复用。

只做四件事：发信封、收信封、按条件等待、干净地收尾。
不做任何协议校验——校验由 Worker 侧做，测试只关心「收到了什么」。
"""

from __future__ import annotations

import itertools
import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable

#: c++-python/
HCPP_DIR = pathlib.Path(__file__).resolve().parents[1]
WORKER_SRC = HCPP_DIR / "worker" / "src"
WORKER_ENTRY = WORKER_SRC / "pet_worker" / "__main__.py"


class WorkerHost:
    def __init__(self, *, log_level: str = "warning", session_id: str = "sess-integration-1") -> None:
        self.session_id = session_id
        self.messages: list[dict] = []
        self.stderr_text = ""
        self._seqs: dict[str, int] = {}
        self._inbox: "queue.Queue[dict | None]" = queue.Queue()
        self._closed = False

        env = os.environ.copy()
        env["PYTHONPATH"] = str(WORKER_SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(WORKER_ENTRY), "--log-level", log_level],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def _drain_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue  # Worker stdout 必须是纯协议流；出现坏行记录给诊断用
            self.messages.append(message)
            self._inbox.put(message)
        self._inbox.put(None)

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for chunk in self.proc.stderr:
            self.stderr_text += chunk.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------
    def send_raw(self, envelope: dict) -> None:
        assert self.proc.stdin is not None
        data = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(
        self,
        msg_type: str,
        payload: dict | None = None,
        *,
        kind: str = "event",
        request_id: str | None = None,
        session_id: str | None = None,
        protocol_version: int = 1,
    ) -> dict:
        if kind == "event":
            rid: Any = None
        else:
            rid = request_id or f"{msg_type}-req"
        kind_bucket = self._seqs
        seq = kind_bucket.get(kind, -1) + 1
        kind_bucket[kind] = seq
        envelope = {
            "protocol_version": protocol_version,
            "session_id": session_id or self.session_id,
            "seq": seq,
            "kind": kind,
            "type": msg_type,
            "pet_id": "main",
            "request_id": rid,
            "payload": payload if payload is not None else {},
        }
        self.send_raw(envelope)
        return envelope

    def send_bytes(self, data: bytes) -> None:
        """绕过信封直接写原始字节：构造非法 JSON / 超限行等敌意输入。"""
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    # ------------------------------------------------------------------
    # 等待
    # ------------------------------------------------------------------
    def wait_for(self, predicate: Callable[[dict], bool], timeout: float = 10.0) -> dict:
        """等待第一条满足条件的入向消息；超时抛 TimeoutError。"""
        for message in self.messages:
            if predicate(message):
                return message
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"等待消息超时（{timeout}s），已收到: {[m.get('type') for m in self.messages]}")
            try:
                item = self._inbox.get(timeout=remaining)
            except queue.Empty:
                continue
            if item is None:
                raise EOFError("Worker stdout 已关闭（进程可能已退出）")
            if predicate(item):
                return item

    def of_type(self, msg_type: str, timeout: float = 10.0) -> dict:
        return self.wait_for(lambda m: m.get("type") == msg_type, timeout)

    def expect_silence(self, msg_type: str, seconds: float = 2.0) -> None:
        """断言在 seconds 内**没有**出现指定类型的消息（负向断言）。"""
        time.sleep(seconds)
        types = [m.get("type") for m in self.messages]
        assert msg_type not in types, f"不应出现 {msg_type}，实际收到: {types}"

    # ------------------------------------------------------------------
    # 完整启动序列
    # ------------------------------------------------------------------
    def prepare(self, config_payload: dict | None = None, pet_payload: dict | None = None) -> dict:
        """hello → ready → config.snapshot → pet.snapshot，回到可提案状态。

        返回前留出 settle 时间：Worker 在自己的主循环里应用快照并启动监视器，
        监视器启动时会对事件文件做 backfill 对齐——在它启动**之前**写入的
        事件会被当成历史吞掉（这正是产品语义）。测试要在 settle 之后写事件。
        """
        hello = self.send(
            "runtime.hello",
            {"host_version": "integration", "required_capabilities": ["state", "behavior", "custom_agent"]},
            kind="request",
            request_id="hello-1",
        )
        ready = self.of_type("runtime.ready")
        assert ready["request_id"] == hello["request_id"], "ready 的 request_id 必须与 hello 一致"
        if config_payload is not None:
            self.send("config.snapshot", config_payload)
        if pet_payload is not None:
            self.send("pet.snapshot", pet_payload)
        if config_payload is not None or pet_payload is not None:
            time.sleep(2.0)  # > 轮询间隔 1.5s：确保快照已应用、监视器已 prime
        return ready

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def close(self, *, graceful: bool = True, timeout: float = 10.0) -> int | None:
        if self._closed:
            return self.proc.poll()
        self._closed = True
        if graceful and self.proc.poll() is None:
            try:
                self.send("runtime.shutdown")
            except (BrokenPipeError, OSError):
                pass
        try:
            self.proc.wait(timeout=timeout if graceful else 0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return self.proc.returncode
