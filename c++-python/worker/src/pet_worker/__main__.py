# -*- coding: utf-8 -*-
"""Worker 入口。

进程模型：**一个读取线程 + 一个业务线程**。

- 读取线程只做「read → 分帧 → 塞进队列」，不做任何业务判断；
- 主线程是唯一写入者与唯一业务执行者：处理入向消息、跑定时器、跑业务回调。
  单写者是可证明的，因此出向队列不需要锁。

退出条件只有三个：收到 ``runtime.shutdown``、stdin EOF（宿主进程没了）、
收到终止信号。除此之外不主动退出——Worker 是宿主的从属进程，不替宿主做决定。

退出码（供宿主的 Supervisor 诊断）：

===  ==========================================
0    正常停止（shutdown / stdin EOF）
1    未预期的内部异常
2    协议会话中止（分帧缓冲无限累积，已主动断开）
3    stdin 已关闭但始终没有完成握手
===  ==========================================
"""

from __future__ import annotations

import argparse
import os
import queue
import signal
import sys
import threading
import time
from typing import Any

from pet_worker import __version__
from pet_worker.agents.manager import AgentManager
from pet_worker.contracts import MAX_MESSAGE_BYTES
from pet_worker.logsetup import (
    configure_stdio_utf8,
    get_logger,
    install_stdout_guard,
    protocol_binary_stream,
    setup_logging,
)
from pet_worker.runtime import Runtime
from pet_worker.scheduler import Scheduler
from pet_worker.transport import Framer, LineWriter, OutboundQueue

#: 读取线程的分块大小。64KiB 与单条消息上限同量级：既不至于把一条消息切得太碎，
#: 也不会让「缓冲上限」这条防线在两次 read 之间被跨过去。
READ_CHUNK = 65536

EXIT_OK = 0
EXIT_INTERNAL_ERROR = 1
EXIT_PROTOCOL_ABORT = 2
EXIT_NO_HANDSHAKE = 3

#: 队列哨兵。宿主 stdin 关闭时投递，避免主线程把「没有消息」误当成「可以继续等」。
_EOF = object()


def _read_chunk(stream: Any, size: int) -> bytes:
    """读一段可用字节。

    必须用 ``read1``（或等价的单次系统调用）而不是 ``read``：``read(n)`` 会一直等到
    攒满 n 字节或 EOF，桌宠点一下到气泡弹出要等 64KiB 是不可能的。
    """
    read1 = getattr(stream, "read1", None)
    if read1 is not None:
        return read1(size)
    return stream.read(size)  # pragma: no cover - 非 BufferedReader 的兜底


def _stdin_reader(framer: Framer, inbox: "queue.Queue[Any]") -> None:
    """读取线程主体：只做搬运，不做业务。"""
    log = get_logger("reader")
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    try:
        while True:
            chunk = _read_chunk(stream, READ_CHUNK)
            if not chunk:
                break
            for message in framer.feed(chunk):
                inbox.put(message)
    except Exception:
        # 读取失败等价于通道失效：按 EOF 处理，让主线程走正常退出路径。
        log.exception("stdin 读取线程异常退出")
    finally:
        if framer.aborted:
            log.error("分帧会话已中止，丢弃剩余字节")
        framer.finish()
        inbox.put(_EOF)


def _run_loop(
    framer: Framer,
    runtime: Runtime,
    scheduler: Scheduler,
    inbox: "queue.Queue[Any]",
    manager: AgentManager,
) -> int:
    """主循环：等消息 → 处理 → 跑定时器 → 冲刷出向队列。

    等待超时取「最近一个定时任务到期时间」与 250ms 的较小值。250ms 是给信号处理
    与队列积压留的响应余量；有定时任务时以定时任务为准，避免空转或延迟 tick。
    """
    log = get_logger("main")
    exit_code = EXIT_OK
    while not runtime.stopping:
        timeout = scheduler.next_delay(maximum=0.25)
        try:
            item = inbox.get(timeout=timeout if timeout and timeout > 0 else 0.0)
        except queue.Empty:
            item = None

        if item is _EOF:
            runtime.handle_eof()
        elif item is not None:
            runtime.handle_inbound(item)

        scheduler.tick()
        runtime.flush()

    runtime.flush()
    # 顺序：先停业务（此时 stdout 仍可用，能记下最终计数），再冲刷协议流。
    manager.stop()
    runtime.shutdown_tasks()
    log.info("主循环结束: reason=%s out=%s", runtime.stop_reason or "unknown", runtime.stats.messages_out)

    if framer.aborted:
        exit_code = EXIT_PROTOCOL_ABORT
    elif not runtime.ready and runtime.stop_reason == "stdin_eof":
        exit_code = EXIT_NO_HANDSHAKE
    if runtime.channel_unhealthy:
        log.warning("出向通道曾不健康: critical_dropped=%s", "见队列统计")
    return exit_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pet-worker", description="Desktop Pet 混合架构的 Python 业务 Worker")
    parser.add_argument("--version", action="version", version=f"pet-worker {__version__}")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="stderr 日志级别（协议通道不受影响）",
    )
    return parser.parse_args(argv)


def _install_signal_handlers(runtime: Runtime) -> None:
    """把 SIGINT/SIGTERM 变成一次正常的停止请求。

    Windows 上 ``TerminateProcess`` 不会触发 Python 信号处理，这条路主要服务于
    手动联调时按 Ctrl+C；宿主侧的强制结束走的是进程终止，不需要 Worker 配合。
    """

    def _handler(signum, _frame):
        get_logger("signal").info("收到信号 %s，开始停止", signum)
        runtime.request_stop(f"signal:{signum}")

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # pragma: no cover - 非主线程/受限平台
            pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 顺序不能反：先切 UTF-8，再装 stdout 守卫，否则原始 stdout 拿到的还是旧编码的流。
    configure_stdio_utf8()
    install_stdout_guard()
    setup_logging(args.log_level.upper())
    log = get_logger("main")
    log.info("pet-worker 启动: version=%s pid=%s", __version__, os.getpid())

    clock = time.monotonic
    framer = Framer(max_message_bytes=MAX_MESSAGE_BYTES)
    writer = LineWriter(protocol_binary_stream(), max_message_bytes=MAX_MESSAGE_BYTES)
    outbound = OutboundQueue()
    scheduler = Scheduler(clock)
    runtime = Runtime(writer, scheduler, outbound=outbound, clock=clock)
    # 业务装配：runtime 只管协议，AgentManager 只管「谁在读、读到了什么、该建议什么」。
    # 三者通过三个回调相连，任一方向都不需要知道对方的内部结构。
    manager = AgentManager(runtime, scheduler=scheduler, clock=clock)
    runtime.on_config_changed = lambda _prev, current: manager.apply_config(current)
    runtime.on_pet_snapshot = lambda _prev, current: manager.apply_pet(current)
    runtime.on_pet_event = manager.on_pet_event

    _install_signal_handlers(runtime)

    inbox: "queue.Queue[Any]" = queue.Queue()
    reader = threading.Thread(target=_stdin_reader, args=(framer, inbox), name="pet-worker-stdin", daemon=True)
    reader.start()

    try:
        return _run_loop(framer, runtime, scheduler, inbox, manager)
    except Exception:
        log.exception("主循环未预期异常")
        return EXIT_INTERNAL_ERROR


def _exit(code: int) -> None:
    """冲刷后立刻结束进程，跳过解释器收尾。

    读取线程几乎总是阻塞在 ``sys.stdin`` 上，而它持有那个 buffered 对象的锁。
    若走正常的 ``sys.exit`` 让解释器收尾，CPython 会抛出
    ``_enter_buffered_busy: could not acquire lock`` 并把退出码变成 crash——
    宿主会以为 Worker「异常退出」而不是「正常结束」，白白触发一次重启。
    因此先显式冲刷，再用 ``os._exit`` 退出。
    """
    for stream in (protocol_binary_stream(), sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def run() -> None:
    """控制台脚本入口。与 ``python -m pet_worker`` 走同一条退出路径。"""
    _exit(main())


if __name__ == "__main__":
    _exit(main())
