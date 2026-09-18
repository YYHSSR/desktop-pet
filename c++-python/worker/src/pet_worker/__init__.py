# -*- coding: utf-8 -*-
"""Desktop Pet 混合架构的 Python 业务 Worker。

契约位置：``c++-python/protocol/envelope.schema.json``。
本包是「业务规则」的唯一实现处：状态归一化、动作轮换、气泡文案等易变策略都在这里；
「此刻是否允许执行」由 C++ 宿主的 ActionArbiter 决定。

硬约束：
- 不 import PySide6 / 任何 GUI 库（一旦引入就等于把渲染层拉回 Python）；
- stdout 只允许输出协议行，日志一律走 stderr；
- 不写 ``config.json``，配置由 C++ 宿主单写，这里只读快照。
"""

from __future__ import annotations

__all__ = ["__version__", "PROTOCOL_VERSION"]

__version__ = "0.1.0"

from pet_worker.contracts import PROTOCOL_VERSION  # noqa: E402  （放在 __version__ 之后便于阅读）
