# -*- coding: utf-8 -*-
"""字节流分帧与出向写入。

对应契约 ``c++-python/protocol/fixtures/framing_cases.json``。C++ 端的
``ProtocolCodec`` 必须与本模块得到完全一致的结果。

分帧判定顺序（顺序错了，超长行的两种用例会得到相反结果）：

1. 先在缓冲区里找 ``\\n``；
2. 找到：取出该整行，**单独**校验这一行的长度，超限记 ``payload_too_large``
   并丢弃该行，不中止会话；
3. 没找到：此时才用**缓冲区总长度**判断，超限立即中止本次协议会话。

整行字节完整之后才做 UTF-8 解码——先解码再切行会把跨读取边界的多字节字符切坏。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pet_worker.contracts import (
    ERR_INVALID_JSON,
    ERR_INVALID_UTF8,
    ERR_PAYLOAD_TOO_LARGE,
    ERR_TRUNCATED_ON_EOF,
    MAX_MESSAGE_BYTES,
    MAX_OUTBOUND_QUEUE_BYTES,
    MAX_OUTBOUND_QUEUE_MESSAGES,
    ProtocolError,
    UnknownMessageType,
    validate_envelope,
)

__all__ = ["Framer", "LineWriter", "OutboundQueue", "encode_line", "measure_line"]


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------
def encode_line(envelope: dict) -> bytes:
    """把信封序列化成一行 UTF-8 字节（含结尾 ``\\n``）。

    ``ensure_ascii=False``：中文文案按 UTF-8 原样传输，两端都显式 UTF-8 编解码。
    字符串内部的换行由 ``json.dumps`` 自动转义，因此一条消息永远只占一行。
    """
    return (json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def measure_line(envelope: dict) -> int:
    return len(encode_line(envelope))


# ----------------------------------------------------------------------
# 分帧器
# ----------------------------------------------------------------------
class Framer:
    """增量字节流分帧器。

    只做「字节 → 合法信封」这一件事，不关心业务。所有异常都被收敛成计数字段，
    除「未终止且超限」之外都不中止会话——一条坏行不该带走整个 Worker。
    """

    #: 最多记住多少个 session 的 seq 水位，防止长跑进程里 dict 无界增长。
    _MAX_TRACKED_SESSIONS = 16

    def __init__(self, max_message_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self.max_message_bytes = max_message_bytes
        self.messages: list[dict] = []
        self.error_counts: Counter[str] = Counter()
        self.unsupported: list[str] = []
        self.duplicates_dropped = 0
        self.aborted = False
        self._buffer = b""
        #: (session_id, kind) → 已见最大 seq。request/response/event 分开计水位：
        #: 两端可能用同一个 session_id，若不按 kind 分开会把合法消息误判为重放。
        self._seen_seq: dict[tuple[str, str], int] = {}

    # -- 状态 ---------------------------------------------------------
    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def error_total(self) -> int:
        return sum(self.error_counts.values())

    def error_list(self) -> list[dict[str, Any]]:
        """按 ``framing_cases.json`` 的 ``expect.errors`` 形状返回。"""
        return [{"kind": kind, "count": count} for kind, count in sorted(self.error_counts.items())]

    # -- 主流程 -------------------------------------------------------
    def feed(self, data: bytes) -> list[dict]:
        """喂入一段到达的字节，返回本次新投递成功的信封列表。"""
        if self.aborted or not data:
            return []
        self._buffer += data
        return self._drain()

    def finish(self) -> None:
        """输入流结束（EOF）。残留半包必须丢弃，绝不当整行解析。"""
        if self.aborted:
            return
        if self._buffer.strip():
            self.error_counts[ERR_TRUNCATED_ON_EOF] += 1
        self._buffer = b""

    def _drain(self) -> list[dict]:
        delivered: list[dict] = []
        while not self.aborted:
            idx = self._buffer.find(b"\n")
            if idx < 0:
                # 规则 3：没有换行时才用总长度判断——这是「无限累积」的唯一出口。
                if len(self._buffer) > self.max_message_bytes:
                    self.error_counts[ERR_PAYLOAD_TOO_LARGE] += 1
                    self._buffer = b""
                    self.aborted = True
                break

            line = self._buffer[:idx]
            self._buffer = self._buffer[idx + 1 :]
            if line.endswith(b"\r"):  # 容忍 CRLF
                line = line[:-1]
            if not line:  # 空行跳过，不算错误
                continue

            # 规则 2：整行长度单独判定，超限只丢这一行。
            if len(line) > self.max_message_bytes:
                self.error_counts[ERR_PAYLOAD_TOO_LARGE] += 1
                continue

            message = self._decode_line(line)
            if message is not None:
                delivered.append(message)
        return delivered

    def _decode_line(self, line: bytes) -> dict | None:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            self.error_counts[ERR_INVALID_UTF8] += 1
            return None

        try:
            obj = json.loads(text)
        except ValueError:
            self.error_counts[ERR_INVALID_JSON] += 1
            return None

        try:
            message = validate_envelope(obj)
        except UnknownMessageType as exc:
            # 信封合法但类型不认识：只记录，绝不触发默认动作。
            self.unsupported.append(exc.message_type)
            return None
        except ProtocolError as exc:
            self.error_counts[exc.kind] += 1
            return None

        if self._is_replay(message):
            self.duplicates_dropped += 1
            return None

        self.messages.append(message)
        return message

    def _is_replay(self, message: dict) -> bool:
        key = (str(message.get("session_id")), str(message.get("kind")))
        seq = message.get("seq")
        if not isinstance(seq, int):
            return False
        previous = self._seen_seq.get(key)
        if previous is not None and seq <= previous:
            return True
        if previous is None and len(self._seen_seq) >= self._MAX_TRACKED_SESSIONS:
            self._seen_seq.pop(next(iter(self._seen_seq)), None)
        self._seen_seq[key] = seq
        return False


# ----------------------------------------------------------------------
# 串行化写入
# ----------------------------------------------------------------------
class LineWriter:
    """唯一的协议写入方。

    出向消息在写出前同样过一遍契约校验：宁可在这里丢一条自己写错的消息，
    也不要让宿主收到非法行、进而判定整条通道损坏并重启 Worker。
    """

    def __init__(self, stream: Any, max_message_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self._stream = stream
        self.max_message_bytes = max_message_bytes
        self.bytes_written = 0
        self.messages_written = 0
        self.write_errors = 0

    def write(self, envelope: dict) -> int | None:
        """写出一个信封，返回写出的字节数；被拒绝或写失败时返回 ``None``。"""
        try:
            validate_envelope(envelope)
        except ProtocolError:
            self.write_errors += 1
            return None

        payload = encode_line(envelope)
        if len(payload) > self.max_message_bytes:
            self.write_errors += 1
            return None

        try:
            self._stream.write(payload)
            self._stream.flush()
        except (OSError, ValueError):
            # 管道已断：宿主可能已经退出。计数，由上层决定是否结束进程。
            self.write_errors += 1
            return None

        self.bytes_written += len(payload)
        self.messages_written += 1
        return len(payload)


# ----------------------------------------------------------------------
# 出向背压队列
# ----------------------------------------------------------------------
@dataclass
class _QueueItem:
    envelope: dict
    size: int
    dedupe_key: str | None = None
    droppable: bool = False


class OutboundQueue:
    """有界出向队列。

    只有主线程会访问它，因此不加锁——单写者是可证明的，比加锁更好维护。

    策略：

    - ``droppable=True`` + ``dedupe_key``：可合并的状态更新，同 key 的旧条目被替换
      （桌宠只需要最新的状态，历史状态没有播放价值）；
    - 队列放不下时先挤掉最老的可丢条目；没有可丢条目时，
      可丢消息直接丢弃（不计为故障），不可丢消息标记 ``unhealthy`` 并计数——
      宿主会据此重连并重新同步快照。
    """

    def __init__(
        self,
        max_messages: int = MAX_OUTBOUND_QUEUE_MESSAGES,
        max_bytes: int = MAX_OUTBOUND_QUEUE_BYTES,
    ) -> None:
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        self._items: list[_QueueItem] = []
        self._bytes = 0
        self.unhealthy = False
        self.dropped_droppable = 0
        self.dropped_critical = 0

    # -- 只读视图 -----------------------------------------------------
    @property
    def pending(self) -> int:
        return len(self._items)

    @property
    def pending_bytes(self) -> int:
        return self._bytes

    def snapshot_keys(self) -> list[str | None]:
        return [item.dedupe_key for item in self._items]

    # -- 写入 ---------------------------------------------------------
    def push(self, envelope: dict, *, dedupe_key: str | None = None, droppable: bool = False) -> bool:
        """入队。返回 ``False`` 表示该条被丢弃。"""
        size = measure_line(envelope)
        if size > MAX_MESSAGE_BYTES:
            raise ProtocolError(ERR_PAYLOAD_TOO_LARGE, f"出向消息 {size} 字节超过上限")

        if dedupe_key is not None:
            self._drop_key(dedupe_key)

        while self._would_overflow(size):
            victim = self._oldest_droppable()
            if victim is None:
                if droppable:
                    self.dropped_droppable += 1
                    return False
                # 关键消息不得静默丢弃：标记通道不健康，让宿主重连后重新同步。
                self.unhealthy = True
                self.dropped_critical += 1
                return False
            self._remove(victim)
            self.dropped_droppable += 1

        self._items.append(_QueueItem(envelope=envelope, size=size, dedupe_key=dedupe_key, droppable=droppable))
        self._bytes += size
        return True

    def pop_all(self) -> list[dict]:
        """取出全部待发信封并清空队列。"""
        envelopes = [item.envelope for item in self._items]
        self._items.clear()
        self._bytes = 0
        return envelopes

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def mark_healthy(self) -> None:
        self.unhealthy = False

    # -- 内部 ---------------------------------------------------------
    def _would_overflow(self, extra: int) -> bool:
        return len(self._items) + 1 > self.max_messages or self._bytes + extra > self.max_bytes

    def _oldest_droppable(self) -> _QueueItem | None:
        for item in self._items:
            if item.droppable:
                return item
        return None

    def _drop_key(self, key: str) -> None:
        for item in list(self._items):
            if item.dedupe_key == key:
                self._remove(item)

    def _remove(self, target: _QueueItem) -> None:
        try:
            self._items.remove(target)
        except ValueError:  # pragma: no cover - 同一对象只可能在列表里出现一次
            return
        self._bytes -= target.size
