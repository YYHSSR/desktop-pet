# -*- coding: utf-8 -*-
"""装配层：把「快照 / 事件来源 / 业务策略 / 出向协议」接在一起。

这一层是 ``__main__`` 与业务之间的唯一缝：``__main__`` 只管进程与线程，
``runtime`` 只管协议，``agents`` 只管读文件，``policies`` 只管出主意。
谁都不知道别人怎么工作，改动一处不会牵动整条链路。

三条纪律：

1. **后端切换是硬开关**。``agent_backend != "python"`` 时监视器一律不轮询，
   绝不与原生实现同时驱动桌宠——旧桌面有原生 ``AgentLinkManager`` 在跑，
   两套来源同时发动作的结果是动作互相打架。
2. **桌宠隐藏就停读**。隐藏期间的轮询纯属浪费，且气泡也弹不出来；
   但只 ``pause`` 不重置 offset，恢复后仍会补读到隐藏期间的事件。
3. **只读、不猜**。事件文件不存在就等它出现（tailer 自己处理），
   不创建目录、不写任何外部位置。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pet_worker.agents.antigravity import AntigravityMonitor
from pet_worker.agents.chatgpt import ChatGptMonitor
from pet_worker.agents.custom_agent import CustomAgentMonitor
from pet_worker.contracts import VALID_STATES
from pet_worker.logsetup import get_logger
from pet_worker.policies.behavior_policy import BehaviorPolicy, Proposal
from pet_worker.runtime import ConfigView, PetView, Runtime
from pet_worker.scheduler import Scheduler

log = get_logger("manager")

#: 本版 Worker 支持的 Agent 类型。将来要加适配器，在这里加分支即可。
SUPPORTED_AGENT_KINDS = ("custom", "antigravity", "chatgpt")


class AgentManager:
    """按快照维护「一组监视器 + 一个策略」，把结论提交给 runtime。"""

    def __init__(
        self,
        runtime: Runtime,
        *,
        scheduler: Scheduler,
        clock: Callable[[], float] = time.monotonic,
        poll_interval_s: float | None = None,
    ) -> None:
        self._runtime = runtime
        self._scheduler = scheduler
        self._clock = clock
        self._poll_interval_s = poll_interval_s

        self._policy = BehaviorPolicy(self._submit, scheduler=scheduler, clock=clock)
        #: agent_key → 监视器；只在「配置里启用且后端为 python」时存在。
        self._monitors: dict[str, CustomAgentMonitor] = {}
        #: agent_key → 当前监听的文件路径，用于识别配置改路径后需要重建监视器。
        self._paths: dict[str, str] = {}
        self._driving = False
        self._closed = False

    # ------------------------------------------------------------------
    # 快照回调（由装配层接到 runtime 的回调上）
    # ------------------------------------------------------------------
    def apply_config(self, config: ConfigView) -> None:
        """应用配置快照：重建 Agent 集合，并按需启动/停止监视器。"""
        if self._closed:
            return
        self._policy.apply_config(config)
        self._driving = config.agent_backend == "python"
        if not self._driving and self._monitors:
            log.info("后端切回 native，停止全部 Agent 监视器")

        wanted: dict[str, str] = {}
        for key, agent in config.agents.items():
            if not agent.enabled:
                continue
            if agent.kind not in SUPPORTED_AGENT_KINDS:
                # 契约已把 kind 限定为支持的三种；这里兜底是为了「宿主比 Worker 新」时
                # 只是少驱动一个 Agent，而不是整份配置作废或抛异常。
                log.warning("不支持的 Agent 类型，已忽略: %s kind=%s", key, agent.kind)
                continue
            if not agent.events_path and agent.kind == "custom":
                # custom 的事件路径来自用户配置，为空就无从读起；
                # antigravity / chatgpt 由宿主下发固定路径（chatgpt 无需路径）。
                log.warning("Agent 未配置事件文件路径，已忽略: %s", key)
                continue
            wanted[key] = (agent.kind, agent.events_path)

        # 先停掉不再需要的，再建新的：顺序反了会出现同名监视器短暂并存。
        for key in list(self._monitors):
            if key not in wanted:
                self._monitors.pop(key).stop()
                self._paths.pop(key, None)

        for key, (kind, path) in wanted.items():
            monitor = self._monitors.get(key)
            if monitor is not None and self._paths.get(key) != (kind, path):
                # 换了类型或事件文件：旧 offset/旧实现对新来源毫无意义，必须重建。
                log.info("Agent 类型或事件文件已变更，重建监视器: %s", key)
                monitor.stop()
                monitor = None
            if monitor is None:
                monitor = self._create_monitor(key, kind, path)
                self._monitors[key] = monitor
                self._paths[key] = (kind, path)

        self._sync_lifecycle()

    def apply_pet(self, pet: PetView) -> None:
        """应用宠物快照：动作目录与可见性都影响策略输出。"""
        if self._closed:
            return
        self._policy.apply_pet(pet)
        self._sync_lifecycle()

    def on_pet_event(self, name: str, payload: dict[str, Any]) -> None:
        """处理宠物事件。只有可见性变化需要在这里响应。

        拖拽开始/结束、代次变化由宿主仲裁器负责（它才知道此刻能不能播），
        Worker 不复制一份相同的状态机——那样两边都会以为自己说了算。
        """
        if self._closed:
            return
        if name == "hidden":
            self._policy.pause()
        elif name == "shown":
            self._policy.resume()
        else:
            return
        self._sync_lifecycle()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """停止全部监视器与待确认的完成通知。退出路径上调用。"""
        if self._closed:
            return
        self._closed = True
        self._policy.close()
        for key in list(self._monitors):
            self._monitors.pop(key).stop()
        self._paths.clear()
        log.info("AgentManager 已停止: counters=%s", self._policy.health())

    def health(self) -> dict[str, Any]:
        """观测用快照：策略计数 + 每个监视器的轮询状态。"""
        return {
            "driving": self._driving,
            "agents": {
                key: {
                    "path": self._paths.get(key, ("", ""))[1],
                    "running": monitor.running,
                    "polling": monitor.polling,
                }
                for key, monitor in self._monitors.items()
            },
            "policy": self._policy.health(),
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _create_monitor(self, agent_key: str, kind: str, path: str) -> Any:
        kwargs: dict[str, Any] = {}
        if self._poll_interval_s is not None:
            kwargs["poll_interval_s"] = self._poll_interval_s
        if kind == "antigravity":
            # antigravity 自己管理 hook 安装与多通道轮询；poll_interval 默认 400ms。
            return AntigravityMonitor(
                agent_key,
                path,
                scheduler=self._scheduler,
                on_event=self._on_event,
                **kwargs,
            )
        if kind == "chatgpt":
            # chatgpt 无事件文件，轮询节奏由快照的 busy 状态自适应。
            return ChatGptMonitor(
                agent_key,
                scheduler=self._scheduler,
                on_event=self._on_event,
            )
        return CustomAgentMonitor(
            agent_key,
            path,
            scheduler=self._scheduler,
            on_event=self._on_event,
            **kwargs,
        )

    def _sync_lifecycle(self) -> None:
        """把「想要的轮询状态」落成一个幂等操作。

        集中在一处判断，避免每个回调各自决定要不要 start/pause，
        出现「隐藏了但某个 Agent 还在读」这类只有现场才能复现的不一致。
        """
        if self._closed:
            return
        visible = bool(self._runtime.pet.visible)
        want_poll = self._driving and visible
        for key, monitor in self._monitors.items():
            if want_poll:
                if not monitor.running:
                    monitor.start()
                else:
                    monitor.resume()
            elif monitor.running:
                monitor.pause()

    def _on_event(self, agent_key: str, state: str, tool: str) -> None:
        """监视器产出：一条状态（可能附带工具名）。

        顺序刻意是「先上报、再决策」：宿主只要收到状态就能更新界面，
        不必等 Worker 把动作想出来；即使策略抛异常，状态也已经发出去了。
        """
        if state in VALID_STATES:
            self._runtime.emit_state_changed(agent_key, state, tool)
        else:
            # Codex 快照还会产出 informational 状态（unavailable / interrupted）：
            # 不在协议词汇表内，不能上协议线；策略侧仍喂原始流，维持完成检测连续性。
            log.debug("非协议状态跳过上报: agent=%s state=%s", agent_key, state)
        self._policy.on_state(agent_key, state)
        if tool:
            self._policy.on_activity(agent_key, tool)

    def _submit(self, proposal: Proposal) -> None:
        """策略建议 → 协议消息。宿主仲裁器决定此刻是否执行。"""
        self._runtime.emit_behavior_propose(
            proposal.action_id,
            agent=proposal.agent or None,
            bubble=proposal.bubble(),
            ttl_ms=proposal.ttl_ms,
        )


__all__ = ["AgentManager", "SUPPORTED_AGENT_KINDS"]
