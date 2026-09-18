# -*- coding: utf-8 -*-
"""本机 Codex（ChatGPT 桌面端）任务监视器。

对照 ``c++/src/services/agent/ChatGptMonitor.{hpp,cpp}`` 移植。

与 ``BaseAgentMonitor`` 的差异：没有事件文件、没有 tailer，数据源是
``CodexStatusReader`` 的同步只读扫描（Python 单线程模型下无须 QtConcurrent 的
worker 池，语义等价——C++ 的「单 worker + 上一轮未完成则跳过」在同步轮询下天然成立）。

自适应轮询用 ``scheduler.after`` 自我重排实现：每拍结束后按 busy 状态决定下一拍
延迟（busy→250ms / 否则→1000ms）。这与 C++ ``m_timer->setInterval(busy ? 250 : 1000)``
等价：QTimer 动态改 interval 影响的是「下一次触发」，``after`` 重排影响的是
「下一次到期」，两者对节拍的语义完全一致。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pet_worker.agents.codex_status import CodexSnapshot, CodexStatusReader
from pet_worker.logsetup import get_logger
from pet_worker.scheduler import Scheduler

log = get_logger("agent")

#: busy 状态下的轮询间隔（秒）。与 C++ m_timer->setInterval(250) 一致。
BUSY_DELAY_S = 0.25
#: 空闲/不可用状态下的轮询间隔（秒）。
IDLE_DELAY_S = 1.0


class ChatGptMonitor:
    """「读 Codex 投影库 → 快照 diff → 发状态/工具」的监视器。

    接口与 ``BaseAgentMonitor`` 兼容（``start`` / ``stop`` / ``pause`` / ``resume`` /
    ``running`` / ``polling``），但刻意不继承它：基类的「tailer + 固定节拍」模型
    对本监视器毫无意义，硬套只会多出一堆死代码。
    """

    def __init__(
        self,
        agent_key: str = "chatgpt",
        *,
        scheduler: Scheduler,
        on_event: Callable[[str, str, str], None],
        reader: CodexStatusReader | None = None,
    ) -> None:
        self.agent_key = agent_key
        self._scheduler = scheduler
        #: ``on_event(agent_key, state, tool)``，与 CustomAgentMonitor 相同约定。
        self._on_event = on_event
        self._reader = reader if reader is not None else CodexStatusReader()
        self._running = False
        self._paused = False
        self._task: Any = None
        self._snapshot = CodexSnapshot()
        #: 下一拍是否用 busy 节奏（由最近一次 publish 决定）。
        self._busy = False

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        """是否已启动（含暂停中）。"""
        return self._running

    @property
    def polling(self) -> bool:
        """是否正在轮询（已启动且未暂停）。"""
        return self._running and not self._paused

    @property
    def snapshot(self) -> CodexSnapshot:
        return self._snapshot

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._paused = False
        # C++ start() 在启动定时器后立即 poll() 一轮：这里用 0 延迟的一拍等价实现。
        self._schedule(0.0)
        log.info("Agent 监视器 [%s] 已启动 (codex 本机状态)", self.agent_key)

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._unschedule()
        self._snapshot = CodexSnapshot()
        log.info("Agent 监视器 [%s] 已停止", self.agent_key)

    def pause(self) -> None:
        if self._running:
            self._paused = True
            self._unschedule()

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            # C++ resume 用 m_timer->start(250)：下一拍 250ms 后，再由 busy 状态接管。
            self._schedule(BUSY_DELAY_S)

    # ------------------------------------------------------------------
    # 轮询
    # ------------------------------------------------------------------
    def poll(self) -> None:
        """手动驱动一轮（生产环境由调度器触发）。未在轮询状态时是空操作。"""
        if not self.polling:
            return
        self._poll_once()

    def _loop(self) -> None:
        self._task = None
        if not self.polling:
            return
        self._poll_once()
        if self.polling:
            # 自适应节拍：与 C++ 在 publish 里 setInterval 等价。
            self._schedule(BUSY_DELAY_S if self._busy else IDLE_DELAY_S)

    def _poll_once(self) -> None:
        # CodexStatusReader 内部已把 sqlite/OS 异常折叠为「暂不可读」快照。
        self._publish(self._reader.read())

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    def _publish(self, snapshot: CodexSnapshot) -> None:
        previous = self._snapshot
        changed_task = (
            previous.thread_id != snapshot.thread_id
            or previous.turn_id != snapshot.turn_id
        )

        self._snapshot = snapshot

        # 节拍换挡在「快照比较」之前：与 C++ 一致，快照没变也要换挡。
        busy = snapshot.state in ("thinking", "working", "attention")
        self._busy = busy

        if snapshot == previous:
            return

        # 基线已完成任务与切换会话都不应庆祝旧历史：
        # 只有被跟踪回合上的真实状态迁移才算完成。
        # task 变化且当前不忙时（例如新回合的首个 idle），状态整体不发——
        # 合并回调（state+tool 一次给出）下工具也一起不发；C++ 里该分支的
        # activity 实际不可达（终态快照 tool 恒为空），语义无损。
        if not changed_task or busy or snapshot.state == "unavailable":
            tool_changed = bool(snapshot.tool) and (
                snapshot.item_id != previous.item_id
                or snapshot.tool != previous.tool
            )
            self._safe_event(snapshot.state, snapshot.tool if tool_changed else "")

    def _safe_event(self, state: str, tool: str) -> None:
        try:
            self._on_event(self.agent_key, state, tool)
        except Exception:  # pragma: no cover - 下游异常不该中断轮询
            log.exception("监视器回调失败: agent=%s", self.agent_key)

    # ------------------------------------------------------------------
    # 调度
    # ------------------------------------------------------------------
    def _schedule(self, delay_s: float) -> None:
        if self._task is not None:
            return
        self._task = self._scheduler.after(
            delay_s, self._loop, name=f"agent.{self.agent_key}"
        )

    def _unschedule(self) -> None:
        if self._task is None:
            return
        self._scheduler.cancel(self._task)
        self._task = None