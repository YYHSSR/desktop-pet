# -*- coding: utf-8 -*-
"""Agent 监视器基类：无 Qt 的生命周期 + 周期轮询。

旧实现用 ``QObject`` + ``QTimer``，这里换成「注入的 ``Scheduler`` + 回调」：
定时器归 Worker 的调度线程所有，监视器只负责按节拍读文件。

生命周期语义与旧实现一致（``start`` / ``stop`` / ``pause`` / ``resume``）：
暂停只是停掉轮询，**不重置 offset**——恢复后从上次位置继续，中间的事件仍会被读到。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pet_worker.agents.protocol import extract_tool, normalize_event_state
from pet_worker.agents.tailer import ByteOffsetTailer
from pet_worker.logsetup import get_logger
from pet_worker.scheduler import Scheduler

log = get_logger("agent")

#: 轮询间隔（秒）。与旧版 QTimer 的 1500ms 保持一致：事件是低频业务，
#: 更密的轮询只会白白唤醒 CPU，更疏的又会让"开始干活"的气泡明显滞后。
POLL_INTERVAL_S = 1.5


class BaseAgentMonitor:
    """按固定节拍把「新事件行」变成「标准状态 + 工具名」回调。

    子类需要覆写 ``_interpret()`` 来适配自己那套事件格式；默认实现走统一协议
    （``event`` / ``state`` / ``tool`` 三个字段）。
    """

    def __init__(
        self,
        agent_key: str,
        tailer: ByteOffsetTailer,
        *,
        scheduler: Scheduler,
        on_event: Callable[[str, str, str], None],
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        self.agent_key = agent_key
        self.tailer = tailer
        self._scheduler = scheduler
        #: ``on_event(agent_key, state, tool)``。state 为空串的行不会回调。
        #: 状态与工具一起给出去：它们是同一条 JSON 行的两个字段，拆成两个回调
        #: 只会让上游把它当成两件事，多出一次「没有状态的工具上报」。
        self._on_event = on_event
        self._poll_interval_s = poll_interval_s

        self._running = False
        self._paused = False
        self._task: Any = None

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

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动监视器：重置读取位置、跳过历史内容、注册周期轮询。

        不创建任何目录——事件文件是用户/外部 Agent 的资产，桌宠只读。
        """
        self._running = True
        self._paused = False
        self.tailer.reset()
        self.tailer.prime()
        self._schedule()
        log.info("Agent 监视器 [%s] 已启动 (%s)", self.agent_key, self.tailer.file_path)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._paused = False
        self._unschedule()
        log.info("Agent 监视器 [%s] 已停止", self.agent_key)

    def pause(self) -> None:
        """暂停轮询但保留读取位置。桌面隐藏、用户暂停联动时用。"""
        if self._running and not self._paused:
            self._paused = True
            self._unschedule()

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            self._schedule()

    # ------------------------------------------------------------------
    # 轮询
    # ------------------------------------------------------------------
    def poll(self) -> int:
        """读取并分发一批新事件，返回产出的状态条数。

        逐行独立处理：任何一行解析失败只丢掉那一行，不影响同一批里的其它事件，
        更不会把异常抛回调度线程（那会让整个 Worker 停止驱动）。
        """
        emitted = 0
        lines = self.tailer.read_new_lines()
        if lines:
            log.debug("轮询 agent=%s 读到 %s 行", self.agent_key, len(lines))
        for line in lines:
            state, tool = self._parse_line(line)
            if not state:
                # 不认识的事件类型：忽略，连工具名一起丢。
                # 绝不能默认当成 working——各 Agent 的 transcript 行类型极其繁杂，
                # 那样桌宠会一直在敲键盘。
                continue
            if self._safe_call(state, tool):
                emitted += 1
        return emitted

    # ------------------------------------------------------------------
    # 供子类覆写
    # ------------------------------------------------------------------
    def _interpret(self, payload: dict) -> tuple[str, str]:
        """把一条事件解析成 ``(state, tool)``；``state`` 为空串表示忽略。"""
        tool = extract_tool(payload)
        event_name = str(payload.get("event", ""))
        explicit_state = str(payload.get("state", ""))
        return normalize_event_state(event_name, explicit_state), tool

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _parse_line(self, line: str) -> tuple[str, str]:
        try:
            payload = json.loads(line)
        except ValueError:
            return "", ""
        if not isinstance(payload, dict):
            return "", ""
        try:
            return self._interpret(payload)
        except Exception:  # pragma: no cover - 子类解析异常不该拖垮轮询
            log.exception("解析事件失败: agent=%s", self.agent_key)
            return "", ""

    def _safe_call(self, state: str, tool: str) -> bool:
        """分发一条状态；下游抛异常只记日志，不让它中断本次读取。"""
        try:
            self._on_event(self.agent_key, state, tool)
        except Exception:  # pragma: no cover - 下游异常不该中断读取
            log.exception("监视器回调失败: agent=%s", self.agent_key)
            return False
        return True

    def _schedule(self) -> None:
        if self._task is not None:
            return
        self._task = self._scheduler.every(
            self._poll_interval_s,
            self.poll,
            name=f"agent.{self.agent_key}",
            immediate=False,
        )

    def _unschedule(self) -> None:
        if self._task is None:
            return
        self._scheduler.cancel(self._task)
        self._task = None
