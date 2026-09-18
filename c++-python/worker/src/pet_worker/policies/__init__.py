# -*- coding: utf-8 -*-
"""业务策略层。

``behavior_policy`` 是唯一的业务规则实现；本包只负责把它暴露出去。
"""

from __future__ import annotations

__all__ = ["BehaviorPolicy", "Proposal"]


def __getattr__(name: str):
    if name in ("BehaviorPolicy", "Proposal"):
        from pet_worker.policies.behavior_policy import BehaviorPolicy, Proposal

        return {"BehaviorPolicy": BehaviorPolicy, "Proposal": Proposal}[name]
    raise AttributeError(name)
