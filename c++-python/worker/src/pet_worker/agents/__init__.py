# -*- coding: utf-8 -*-
"""Agent 事件来源适配层。

本层只做一件事：把「外部 Agent 产生的事件文件」变成「标准状态词汇 + 工具名」，
再交给 policies 层决定该做什么。它不认识桌宠动作、不认识气泡，也不认识协议信封。

与旧实现（``python/pet/services/agent_link.py``）的关系是**行级对照移植**，不是
import 复用——那个模块顶部就 import PySide6，一旦被 Worker 引到，就等于把第二套
GUI 重新拉进进程。
"""

from __future__ import annotations

__all__ = ["AgentManager", "BaseAgentMonitor", "ByteOffsetTailer", "CustomAgentMonitor"]


def __getattr__(name: str):  # 延迟导入：import agents.protocol 时不该连带拉起监视器
    if name == "AgentManager":
        from pet_worker.agents.manager import AgentManager

        return AgentManager
    if name == "BaseAgentMonitor":
        from pet_worker.agents.base import BaseAgentMonitor

        return BaseAgentMonitor
    if name == "ByteOffsetTailer":
        from pet_worker.agents.tailer import ByteOffsetTailer

        return ByteOffsetTailer
    if name == "CustomAgentMonitor":
        from pet_worker.agents.custom_agent import CustomAgentMonitor

        return CustomAgentMonitor
    raise AttributeError(name)
