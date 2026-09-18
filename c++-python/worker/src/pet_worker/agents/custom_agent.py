# -*- coding: utf-8 -*-
"""自定义联动 Agent 适配器（``agent_link.custom_agents`` 配置驱动）。

只读监听用户指定路径的统一协议 JSONL 事件文件（``docs/AGENT_LINK_PROTOCOL.md`` §4）：

- **不创建目录**、不写任何外部位置、不需要授权弹窗；
- 文件不存在时静默空转等待，出现后自动开始增量读取；
- 首次读取做 backfill 防护，跳过历史内容，不重放旧事件。

「只读」不是一句口号：这个路径来自用户配置，可能是任何地方（另一个工具的工作目录、
共享盘、甚至只读挂载）。替用户在那里 mkdir 是越界行为，旧实现专门覆写 ``start()``
来绕开基类的 mkdir，这里把"不 mkdir"作为基线的唯一行为。
"""

from __future__ import annotations

from pathlib import Path

from pet_worker.agents.base import BaseAgentMonitor
from pet_worker.agents.tailer import ByteOffsetTailer


class CustomAgentMonitor(BaseAgentMonitor):
    """统一协议 JSONL 的只读监视器。"""

    def __init__(
        self,
        agent_key: str,
        events_path: str | Path,
        *,
        scheduler,
        on_event,
        poll_interval_s: float | None = None,
    ) -> None:
        # expanduser：配置里写 ~/... 是常态，Worker 的环境变量里没有 shell 帮它展开。
        self.events_file = Path(events_path).expanduser()
        self.events_dir = self.events_file.parent
        kwargs = {}
        if poll_interval_s is not None:
            kwargs["poll_interval_s"] = poll_interval_s
        super().__init__(
            agent_key,
            ByteOffsetTailer(self.events_file),
            scheduler=scheduler,
            on_event=on_event,
            **kwargs,
        )

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<CustomAgentMonitor {self.agent_key} {self.events_file}>"
