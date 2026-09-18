# -*- coding: utf-8 -*-
"""日志与 stdout 守卫。

本项目里 stdout 是协议通道，任何非协议字节都会让宿主判定协议损坏并重连。
因此本模块做两件事：

1. ``setup_logging()``：把日志固定到 stderr，禁止任何 handler 指向 stdout；
2. ``install_stdout_guard()``：把 ``sys.stdout`` 换成一个转向 stderr 的代理，
   杜绝第三方库或调试 ``print`` 污染协议流。真正的协议写入方通过
   ``protocol_stream()`` 拿到被替换前的原始 stdout。

守卫只记录第一个污染者的来源，避免刷屏。
"""

from __future__ import annotations

import io
import logging
import sys
import traceback
from typing import Any

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_LOGGER_NAME = "pet_worker"

_real_stdout: io.TextIOBase | None = None
_guard_installed = False
_warned_once = False


def setup_logging(level: int | str = logging.INFO) -> logging.Logger:
    """把 ``pet_worker`` 日志固定输出到 stderr。

    重复调用是安全的（会移除既有 handler 后重建）。
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - 关闭失败不应中断启动
            pass
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    # 协议层不允许把整行 payload 打进日志；这里只做去重，不做内容审查。
    logger.propagate = False
    return logger


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{suffix}" if suffix else _LOGGER_NAME)


class _StderrProxy(io.TextIOBase):
    """``sys.stdout`` 的替身：写入重定向到 stderr，并告警一次。"""

    def __init__(self, target: io.TextIOBase) -> None:
        self._target = target

    def writable(self) -> bool:  # pragma: no cover - 协议层不依赖
        return True

    def write(self, text: str) -> int:  # type: ignore[override]
        global _warned_once
        if text:
            if not _warned_once:
                _warned_once = True
                origin = "".join(traceback.format_stack(limit=4)[:-1]).strip()
                get_logger("stdout_guard").warning(
                    "检测到对 stdout 的写入，已重定向到 stderr（stdout 是协议通道，禁止写入）：\n%s",
                    origin,
                )
            self._target.write(text)
            self._target.flush()
        return len(text)

    def writelines(self, lines) -> None:  # type: ignore[override]
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._target.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        # 返回 stderr 的 fd：即便有人绕过 Python 层做 os.write，也不会写进协议通道。
        return self._target.fileno()


def install_stdout_guard() -> io.TextIOBase:
    """安装 stdout 守卫，返回被替换前的原始 stdout（协议写入方使用）。"""
    global _real_stdout, _guard_installed
    if _guard_installed and _real_stdout is not None:
        return _real_stdout
    _real_stdout = sys.stdout
    sys.stdout = _StderrProxy(sys.stderr)
    _guard_installed = True
    return _real_stdout


def protocol_stream() -> io.TextIOBase:
    """协议写入目标（文本层）。未安装守卫时退化为当前 stdout。"""
    return _real_stdout if _real_stdout is not None else sys.stdout


def protocol_binary_stream() -> Any:
    """协议写入目标的**字节层**。

    协议行是字节，必须绕过文本层：文本层会做编码与换行转换，而我们已经自己完成了
    UTF-8 编码和 ``\\n`` 结尾——再套一层只会让长度统计与实际写出量对不上。
    """
    stream = protocol_stream()
    return getattr(stream, "buffer", stream)


def configure_stdio_utf8() -> None:
    """把三个标准流切到 UTF-8 + 行缓冲。

    Windows 默认代码页不是 UTF-8，中文文案会直接抛 UnicodeEncodeError。
    ``errors="replace"`` 是最后一道保险：宁可让某条消息降级，也不能因为
    一个字符把整个 Worker 带走。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", newline="\n", write_through=True)
        except Exception:  # pragma: no cover - 某些被替换过的流不支持
            pass
