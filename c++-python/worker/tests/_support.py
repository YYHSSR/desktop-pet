# -*- coding: utf-8 -*-
"""单测共用的小工具：假时钟、内存流、信封构造、夹具加载。

刻意不引入 pytest 之外的依赖，也不启动任何子进程——跨进程的部分属于
``c++-python/tests`` 的集成测试，这里只验证纯逻辑。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

#: ``c++-python/``
HCPP_DIR = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_DIR = HCPP_DIR / "protocol" / "fixtures"
VALID_DIR = FIXTURE_DIR / "valid"


class FakeClock:
    """可控单调时钟。

    策略里几乎每个判断都带时间门槛（去抖 2s、完成确认 800ms、冷却 5s），
    真等只会让测试又慢又飘；直接推进假时钟才能精确验证边界。
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class MemoryStream:
    """最小可写流，代替真实 stdout。"""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ValueError("stream closed")
        self.chunks.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    @property
    def raw(self) -> bytes:
        return b"".join(self.chunks)

    def envelopes(self) -> list[dict]:
        text = self.raw.decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def types(self) -> list[str]:
        return [str(item.get("type")) for item in self.envelopes()]


def envelope(
    msg_type: str,
    payload: dict | None = None,
    *,
    kind: str = "event",
    session_id: str = "sess-test-1",
    seq: int = 0,
    request_id: str | None = None,
    pet_id: str = "main",
    protocol_version: int = 1,
) -> dict:
    """构造一条合法信封。

    ``request_id`` 的取值由 kind 决定（request/response 必须非空，event 必须为 null），
    这是契约里最容易被手写测试写错的一处，因此在这里统一处理。
    """
    if kind == "event":
        actual_request_id: Any = None
    else:
        actual_request_id = request_id or f"{msg_type}-req"
    return {
        "protocol_version": protocol_version,
        "session_id": session_id,
        "seq": seq,
        "kind": kind,
        "type": msg_type,
        "pet_id": pet_id,
        "request_id": actual_request_id,
        "payload": payload if payload is not None else {},
    }


def load_valid(name: str) -> dict:
    """加载 ``protocol/fixtures/valid/<name>.json``。"""
    return json.loads((VALID_DIR / f"{name}.json").read_text(encoding="utf-8"))


def load_framing_cases() -> dict:
    return json.loads((FIXTURE_DIR / "framing_cases.json").read_text(encoding="utf-8"))
