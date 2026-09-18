# -*- coding: utf-8 -*-
"""有界 Byte-Offset 文件增量行读取器。

对照 ``python/pet/services/agent_link.py::ByteOffsetTailer`` 逐行移植（去掉 Qt 依赖）。
这里**不做任何"优化"**：所有分支都对应一个真实踩过的坑，删掉任何一条都会让桌宠
在某个特定写入方式下重放历史或丢事件。

- 首次读取做 backfill 防护：跳到当前末尾，绝不重放历史事件；
- 文件截断，或同路径被换成新文件（rename 轮转）时重置到头部；
  只看 size 不够：新文件可能在下次轮询前就长到不小于旧 offset，那样会永久跳过新文件前部；
- 单次读取有界（默认 64KiB），避免大文件一次性读入卡住调度；
- 半行缓冲：``read()`` 边界与写入方未写完都会产生半行，绝不把半行当整行解析；
- 超长行丢弃：单行超过上限时进入丢弃模式，跳到下一个换行再恢复，
  否则那半截会被当成一条新事件。
"""

from __future__ import annotations

import os
from pathlib import Path

from pet_worker.logsetup import get_logger

log = get_logger("tailer")

DEFAULT_MAX_CHUNK_BYTES = 65536


class ByteOffsetTailer:
    """记录 byte offset 的增量行读取器。零外部依赖，毫秒级读取。"""

    def __init__(self, file_path: Path | str, max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES) -> None:
        self.file_path = Path(file_path)
        self.offset: int = 0
        self.max_chunk_bytes = max_chunk_bytes
        self._initial_backfill_done = False
        #: 跨读取边界的未完成行缓冲（防止半行被丢弃）
        self._partial: bytes = b""
        #: 超长行丢弃模式：跳到下一个换行再恢复
        self._discard_until_newline = False
        #: 文件身份（Win: ino+ctime_ns / POSIX: dev+ino），用于识别同路径轮转
        self._file_id: tuple[int, ...] | None = None

    def reset(self) -> None:
        """回到"尚未读过任何内容"的状态。下次读取会重新做 backfill 防护。"""
        self.offset = 0
        self._initial_backfill_done = False
        self._partial = b""
        self._discard_until_newline = False
        self._file_id = None

    def prime(self) -> None:
        """立刻完成 backfill：把 offset 对齐到当前末尾，跳过既有历史内容。

        与「首次 ``read_new_lines()``」等价，区别只是**时机**：监视器一启动就生效，
        不会留下一个「已启动但还没读过」的窗口——那个窗口里写入的事件会被 backfill
        当成历史丢掉，用户看到的现象是"刚打开桌宠，Agent 的第一件事没反应"。

        文件此刻不存在就什么都不做；首次真正读到文件时仍会走 backfill 防护。
        """
        try:
            st = self.file_path.stat()
        except (OSError, AttributeError):
            return
        if not self._initial_backfill_done:
            self._initial_backfill_done = True
            self.offset = st.st_size
            self._file_id = self._file_identity(st)
            self._partial = b""

    def read_new_lines(self) -> list[str]:
        """读取自上次 offset 以来的全部**完整**新增行。

        返回已 strip 且过滤空行的字符串列表；文件不存在或读失败返回空列表
        （静默空转等待，不抛异常——监视器不该因为文件暂时不可读而崩溃）。
        """
        if not self.file_path.is_file():
            return []

        try:
            st = self.file_path.stat()
            size = st.st_size
            file_id = self._file_identity(st)
        except (OSError, AttributeError):
            return []

        if not self._initial_backfill_done:
            self._initial_backfill_done = True
            self.offset = size
            self._file_id = file_id
            self._partial = b""
            return []

        if size < self.offset or (self._file_id is not None and file_id != self._file_id):
            self.offset = 0
            self._partial = b""
            # 旧文件的超长行丢弃状态不得泄漏进新文件
            self._discard_until_newline = False
        self._file_id = file_id

        if size == self.offset:
            return []

        bytes_to_read = min(size - self.offset, self.max_chunk_bytes)
        try:
            with open(self.file_path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(bytes_to_read)
                self.offset = handle.tell()
        except OSError as exc:
            log.warning("读取 tail 文件失败 %s: %s", self.file_path, exc)
            return []

        chunk = self._partial + chunk
        chunk = self._skip_discarded_line(chunk)
        if chunk is None:
            return []

        chunk = self._split_trailing_partial(chunk)
        return self._decode_lines(chunk)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _file_identity(st: os.stat_result) -> tuple[int, ...]:
        """文件身份识别，应对 rename 轮转出同路径新文件。

        Windows 用 ``(ino, ctime_ns)``——ctime 是创建时间，追加不变、轮转变化；
        POSIX 的 ctime 是 inode 变更时间（每次追加都变），只能用 ``(dev, ino)``。
        """
        if os.name == "nt":
            return (st.st_ino, st.st_ctime_ns)
        return (st.st_dev, st.st_ino)

    def _skip_discarded_line(self, chunk: bytes) -> bytes | None:
        """超长行丢弃模式：跳到下一个换行再恢复。找不到换行则返回 ``None``。"""
        if not self._discard_until_newline:
            return chunk
        idx = chunk.find(b"\n")
        if idx == -1:
            return None
        self._discard_until_newline = False
        return chunk[idx + 1:]

    def _split_trailing_partial(self, chunk: bytes) -> bytes:
        """把末尾不完整的半行挪进 ``_partial``，只返回完整的部分。"""
        if not chunk or chunk.endswith(b"\n"):
            self._partial = b""
            return chunk

        idx = chunk.rfind(b"\n")
        if idx == -1:
            self._partial = chunk
            complete = b""
        else:
            self._partial = chunk[idx + 1:]
            complete = chunk[: idx + 1]

        # 防呆：单行超过上限时进入丢弃模式，跳到下一个换行再恢复，避免把它的"后半截"
        # 当成一条新事件解析。
        # 两个分支都要判：整块连一个换行都没有时 ``_partial`` 会跨读取不断累积，而那正是
        # 超长行最常见的形态（一行的长度往往远大于一次读取的上界）。只判后半段的话，
        # 行长到上限之后还会继续攒，直到换行到来——届时整行都会被当作一行吐出来。
        if len(self._partial) > self.max_chunk_bytes:
            log.warning("tail 行超过 %d 字节上限，丢弃该超长行: %s", self.max_chunk_bytes, self.file_path)
            self._partial = b""
            self._discard_until_newline = True
        return complete

    @staticmethod
    def _decode_lines(chunk: bytes) -> list[str]:
        """整行字节完整后才解码。

        ``utf-8-sig``：兼容 PowerShell ``Add-Content -Encoding UTF8`` 写在首行的 BOM。
        ``errors="replace"``：单行内容坏了也只影响这一行，不该让整个监视器停摆。
        """
        text = chunk.decode("utf-8-sig", errors="replace")
        return [line.strip() for line in text.splitlines() if line.strip()]
