# -*- coding: utf-8 -*-
"""单线程定时调度。

Worker 是单主线程模型：所有业务回调、协议写入、定时器都在同一个线程里跑，
因此不需要锁。代价是任何回调都不能阻塞——回调里只允许做「读一个小文件的增量」
和「往出向队列里塞一条消息」这两类操作。

时钟用 ``time.monotonic``：系统时间被校准时不能让「2 秒 TTL」变成 2 小时。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from pet_worker.logsetup import get_logger

log = get_logger("scheduler")

__all__ = ["Scheduler", "Task"]


@dataclass
class Task:
    name: str
    callback: Callable[[], None]
    deadline: float
    interval: float | None = None  # None 表示一次性任务
    cancelled: bool = False


class Scheduler:
    """最小可用定时器集合：``every`` / ``after`` / ``cancel`` / ``tick``。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tasks: list[Task] = []

    # -- 注册 ---------------------------------------------------------
    def every(
        self,
        interval_s: float,
        callback: Callable[[], None],
        *,
        name: str = "",
        immediate: bool = False,
    ) -> Task:
        if interval_s <= 0:
            raise ValueError("周期必须大于 0")
        now = self._clock()
        task = Task(
            name=name or getattr(callback, "__name__", "task"),
            callback=callback,
            deadline=now if immediate else now + interval_s,
            interval=interval_s,
        )
        self._tasks.append(task)
        return task

    def after(self, delay_s: float, callback: Callable[[], None], *, name: str = "") -> Task:
        now = self._clock()
        task = Task(
            name=name or getattr(callback, "__name__", "task"),
            callback=callback,
            deadline=now + max(0.0, delay_s),
            interval=None,
        )
        self._tasks.append(task)
        return task

    def cancel(self, task: Task) -> None:
        task.cancelled = True
        try:
            self._tasks.remove(task)
        except ValueError:
            pass

    def cancel_all(self) -> None:
        for task in self._tasks:
            task.cancelled = True
        self._tasks.clear()

    # -- 运行 ---------------------------------------------------------
    @property
    def pending(self) -> int:
        return len(self._tasks)

    def next_delay(self, *, maximum: float | None = None) -> float | None:
        """距离最近一个到期任务还有多少秒；没有任务时返回 ``maximum``（可为 None）。

        主循环用它作为等待新消息的超时——绝不能返回 ``None`` 后去无限等待，
        否则有定时任务的 Worker 会永远不醒。
        """
        if not self._tasks:
            return maximum
        delta = min(task.deadline for task in self._tasks) - self._clock()
        if maximum is not None:
            return max(0.0, min(delta, maximum))
        return max(0.0, delta)

    def tick(self) -> int:
        """执行所有到期任务，返回执行数量。

        回调抛异常只记日志并继续——一个坏任务不该让整个 Worker 停摆。
        周期任务在执行后重排下一次到期时间（用 deadline 累加而非 now+interval，
        避免每次执行的耗散累积成漂移）。
        """
        now = self._clock()
        due = [task for task in self._tasks if not task.cancelled and task.deadline <= now]
        for task in due:
            if task.cancelled:
                continue
            try:
                task.callback()
            except Exception:
                log.exception("定时任务执行失败: %s", task.name)
            if task.cancelled:
                continue
            if task.interval is None:
                try:
                    self._tasks.remove(task)
                except ValueError:
                    pass
            else:
                # 落后太多（例如进程被挂起）时跳到下一个未来时刻，而不是补跑一串历史 tick。
                task.deadline += task.interval
                if task.deadline <= now:
                    missed = int((now - task.deadline) // task.interval) + 1
                    task.deadline += missed * task.interval
        return len(due)
