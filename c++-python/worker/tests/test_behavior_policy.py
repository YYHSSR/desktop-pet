# -*- coding: utf-8 -*-
"""行为策略测试：去抖、节流、动作轮换、完成确认、冷却、隐藏处理。

基准是旧版 ``python/pet/services/agent_link.py::AgentLinkManager`` 的常量与语义。
沿用默认值而不是在测试里改小阈值，是为了让「移植是否等价」这件事可验证。
"""

from __future__ import annotations

from _support import FakeClock

import pytest
from pet_worker.contracts import SYSTEM_ACTION_BUBBLE, SYSTEM_ACTION_IDLE
from pet_worker.policies.behavior_policy import BUBBLE_MS_ACTIVITY, BehaviorPolicy
from pet_worker.runtime import AgentConfig, ConfigView, PetView, PolicyConfig
from pet_worker.scheduler import Scheduler

#: 动作名刻意与真实角色包一致：策略靠名字关键词匹配，用假名字测不出问题。
ACTIONS = ("rand/写代码", "rand/吃Token", "rand/轻快记录", "rand/漂浮踏步")


def _agent(key: str = "a1", display: str = "Claude", **overrides) -> AgentConfig:
    return AgentConfig(
        key=key,
        enabled=True,
        kind="custom",
        display_name=display,
        events_path=f"{key}.jsonl",
        **overrides,
    )


def make_policy(
    *,
    actions: tuple[str, ...] = ACTIONS,
    agents: dict[str, AgentConfig] | None = None,
    visible: bool = True,
    **policy_overrides,
) -> tuple[BehaviorPolicy, list, FakeClock, Scheduler]:
    clock = FakeClock()
    scheduler = Scheduler(clock)
    out: list = []
    policy = BehaviorPolicy(out.append, scheduler=scheduler, clock=clock)
    policy.apply_config(
        ConfigView(
            revision=7,
            agent_backend="python",
            agents=agents if agents is not None else {"a1": _agent()},
            policy=PolicyConfig(**policy_overrides),
        )
    )
    policy.apply_pet(PetView(generation=3, character="random", visible=visible, actions=actions))
    return policy, out, clock, scheduler


def actions_of(out: list) -> list[str]:
    return [item.action_id for item in out]


def bubbles_of(out: list) -> list[str]:
    return [item.bubble_text for item in out if item.bubble_text]


def bubble_proposals(out: list) -> list:
    return [item for item in out if item.bubble_text]


# ----------------------------------------------------------------------
# 默认值
# ----------------------------------------------------------------------
def test_default_policy_matches_legacy_values() -> None:
    """默认值必须与旧版 Python 实现一致，否则「切后端」会变成「换行为」。"""
    config = PolicyConfig()
    assert config.notify_state is False
    assert config.notify_done is True
    assert config.notify_activity is False
    assert config.min_interval_ms == 2000
    assert config.activity_interval_ms == 10000
    assert config.activity_global_min_ms == 8000
    assert config.activity_same_label_ms == 60000
    assert config.done_confirm_ms == 800
    assert config.done_cooldown_ms == 5000


def test_policy_from_payload_ignores_unknown_and_invalid() -> None:
    """宿主的配置比 Worker 新时，多出来的键不该让整份策略作废。"""
    config = PolicyConfig.from_payload(
        {"notify_done": False, "done_confirm_ms": 1500, "unknown_key": 1, "min_interval_ms": "nope"}
    )
    assert config.notify_done is False
    assert config.done_confirm_ms == 1500
    assert config.min_interval_ms == 2000  # 非法值回落到默认
    assert PolicyConfig.from_payload(None).done_confirm_ms == 800


# ----------------------------------------------------------------------
# 动作轮换
# ----------------------------------------------------------------------
def test_link_action_rotation_interleaves_break() -> None:
    policy, out, clock, _ = make_policy()
    for state in ("working", "thinking", "working", "thinking", "working"):
        policy.on_state("a1", state)
        clock.advance(2.5)
    assert actions_of(out) == [
        "rand/写代码",   # 第 1 次：主动作 1
        "rand/吃Token",  # 第 2 次：主动作 2
        "rand/轻快记录",  # 第 3 次：摸鱼
        "rand/吃Token",
        "rand/写代码",
    ]


def test_link_action_falls_back_when_names_unmatched() -> None:
    """角色包动作名不含关键词时仍要有动作可播，而不是安静地什么都不做。"""
    policy, out, clock, _ = make_policy(actions=("rand/foo", "rand/bar"))
    policy.on_state("a1", "working")
    assert actions_of(out) == ["rand/foo"]


def test_link_action_falls_back_to_keywords() -> None:
    """精确名缺失时按语义关键词挑（真实角色包的动作名并不统一）。"""
    policy, out, clock, _ = make_policy(actions=("rand/闲坐写写代码", "rand/伸伸懒腰"))
    policy.on_state("a1", "working")
    assert actions_of(out) == ["rand/闲坐写写代码"]


# ----------------------------------------------------------------------
# 去抖与节流
# ----------------------------------------------------------------------
def test_repeated_state_is_debounced() -> None:
    policy, out, clock, _ = make_policy()
    policy.on_state("a1", "working")
    clock.advance(30.0)
    policy.on_state("a1", "working")
    assert len(out) == 1
    assert policy.health()["debounced"] == 1


def test_rapid_state_switch_is_throttled() -> None:
    """2 秒内换状态不生效：Agent 的 working/thinking 抖动不该让桌宠抽风。"""
    policy, out, clock, _ = make_policy()
    policy.on_state("a1", "working")
    clock.advance(0.5)
    policy.on_state("a1", "attention")
    assert len(out) == 1
    assert policy.health()["throttled"] == 1

    clock.advance(2.0)
    policy.on_state("a1", "attention")
    assert bubbles_of(out) == ["主人，Agent 这边需要你看一眼～"]


def test_attention_without_busy_bubbles_and_is_important() -> None:
    policy, out, clock, _ = make_policy()
    policy.on_state("a1", "attention")
    assert len(out) == 1
    assert out[0].bubble_important is True
    assert out[0].action_id == SYSTEM_ACTION_BUBBLE


# ----------------------------------------------------------------------
# 工具事件
# ----------------------------------------------------------------------
def test_tool_event_picks_action_by_keyword() -> None:
    policy, out, clock, _ = make_policy(actions=("rand/写代码", "rand/看", "rand/轻快记录"))
    policy.on_activity("a1", "bash")
    assert actions_of(out) == ["rand/写代码"]

    clock.advance(1.0)
    policy.on_activity("a1", "read")
    assert len(out) == 1  # 2 秒内不重复因工具触发动作
    assert policy.health()["tool_anim_skipped"] == 1

    clock.advance(2.0)
    policy.on_activity("a1", "read")
    assert actions_of(out)[-1] in {"rand/看", "rand/轻快记录"}


def test_unknown_tool_does_not_move_pet() -> None:
    """认不出来的工具宁可不播，也别乱播一个动作。"""
    policy, out, clock, _ = make_policy()
    policy.on_activity("a1", "some_vendor_specific_tool_xyz")
    assert out == []


def test_activity_bubble_needs_opt_in_and_throttles() -> None:
    agents = {"a1": _agent("a1", "Claude"), "a2": _agent("a2", "Copilot"), "a3": _agent("a3", "Cursor")}
    policy, out, clock, _ = make_policy(agents=agents, notify_activity=False)
    policy.on_activity("a1", "bash")
    assert bubbles_of(out) == []  # 默认关闭过程汇报

    policy, out, clock, _ = make_policy(agents=agents, notify_activity=True)
    policy.on_activity("a1", "bash")
    assert bubbles_of(out) == ["Claude 正在跑命令…"]
    assert bubble_proposals(out)[0].bubble_duration_ms == BUBBLE_MS_ACTIVITY

    clock.advance(1.0)
    policy.on_activity("a1", "bash")  # 同一 Agent 10s 内不重复
    assert policy.health()["activity_throttled"] == 1

    clock.advance(9.0)
    policy.on_activity("a1", "bash")  # 同一文案 60s 内不重复
    assert bubbles_of(out) == ["Claude 正在跑命令…"]

    clock.advance(9.0)
    policy.on_activity("a2", "bash")  # 换 Agent：能发
    assert bubbles_of(out) == ["Claude 正在跑命令…", "Copilot 正在跑命令…"]

    clock.advance(1.0)
    policy.on_activity("a3", "bash")  # 全局 8s 最小间隔：拦下
    assert len(bubbles_of(out)) == 2


# ----------------------------------------------------------------------
# 完成确认
# ----------------------------------------------------------------------
def test_done_confirmation_ignores_flapping() -> None:
    """working → idle → working：回到忙就取消，不弹「干完活啦」。"""
    policy, out, clock, scheduler = make_policy()
    policy.on_state("a1", "working")
    clock.advance(3.0)
    policy.on_state("a1", "idle")
    assert scheduler.pending == 1

    clock.advance(0.3)
    policy.on_state("a1", "working")
    assert scheduler.pending == 0

    clock.advance(5.0)
    scheduler.tick()
    assert "Claude 干完活啦，去看看成果吧～" not in bubbles_of(out)
    assert policy.health()["done_confirmed"] == 0


def test_done_notification_is_important_and_separate_from_action() -> None:
    """通知与动作分开提交：动作可能被宿主拒绝，通知不该跟着丢。"""
    policy, out, clock, scheduler = make_policy()
    policy.on_state("a1", "working")
    clock.advance(3.0)
    policy.on_state("a1", "idle")
    clock.advance(0.9)
    scheduler.tick()

    proposals = bubble_proposals(out)
    assert [item.bubble_text for item in proposals] == ["Claude 干完活啦，去看看成果吧～"]
    assert proposals[0].bubble_important is True
    assert proposals[0].action_id == SYSTEM_ACTION_BUBBLE
    assert policy.health()["done_confirmed"] == 1


def test_done_cooldown_suppresses_repeat() -> None:
    policy, out, clock, scheduler = make_policy(done_cooldown_ms=20000)
    for _ in range(2):
        policy.on_state("a1", "working")
        clock.advance(2.5)
        policy.on_state("a1", "idle")
        clock.advance(2.5)
        clock.advance(0.9)
        scheduler.tick()
    assert len(bubble_proposals(out)) == 1
    assert policy.health()["done_suppressed"] == 1


def test_done_cooldown_disabled_by_zero() -> None:
    policy, out, clock, scheduler = make_policy(done_cooldown_ms=0)
    for _ in range(2):
        policy.on_state("a1", "working")
        clock.advance(2.5)
        policy.on_state("a1", "idle")
        clock.advance(2.5)
        clock.advance(0.9)
        scheduler.tick()
    assert len(bubble_proposals(out)) == 2


def test_busy_then_alert_hedges_done_text() -> None:
    """busy 期间出现过 attention/error：不暗示「成功完成」。"""
    policy, out, clock, scheduler = make_policy()
    policy.on_state("a1", "working")
    clock.advance(3.0)
    policy.on_state("a1", "attention")
    assert bubbles_of(out) == []  # busy 后的 attention 由完成确认接管

    clock.advance(0.9)
    scheduler.tick()
    assert bubbles_of(out) == ["Claude 那边停了，结果怎么样要主人自己看一眼哦～"]


def test_other_agent_still_busy_keeps_action() -> None:
    """A 完成不顶掉 B 的工作动画。"""
    agents = {"a1": _agent("a1", "Claude"), "a2": _agent("a2", "Copilot")}
    policy, out, clock, scheduler = make_policy(agents=agents)
    policy.on_state("a2", "working")
    clock.advance(3.0)
    policy.on_state("a1", "working")
    policy.on_state("a1", "idle")
    clock.advance(0.9)
    scheduler.tick()
    assert SYSTEM_ACTION_IDLE not in actions_of(out)
    assert bubbles_of(out) == ["Claude 干完活啦，去看看成果吧～"]


# ----------------------------------------------------------------------
# 开始干活与文案
# ----------------------------------------------------------------------
def test_start_bubble_only_on_leaving_idle() -> None:
    """thinking ↔ working 互跳不重复播报「开始干活」。"""
    policy, out, clock, _ = make_policy(notify_state=True)
    policy.on_state("a1", "working")
    assert bubbles_of(out) == ["Claude 开始干活啦～随时为你效劳！"]

    clock.advance(3.0)
    policy.on_state("a1", "thinking")
    assert len(bubbles_of(out)) == 1


def test_thinking_uses_agent_custom_text() -> None:
    agents = {"a1": _agent("a1", "Claude", thinking_text="帮主人琢磨「{name}」的问题…")}
    policy, out, clock, _ = make_policy(agents=agents, notify_state=True)
    policy.on_state("a1", "thinking")
    assert bubbles_of(out) == ["帮主人琢磨「Claude」的问题…"]


# ----------------------------------------------------------------------
# 可见性与收尾
# ----------------------------------------------------------------------
def test_hidden_pauses_suggestions_and_cancels_pending_done() -> None:
    policy, out, clock, scheduler = make_policy()
    policy.on_state("a1", "working")
    clock.advance(3.0)
    policy.on_state("a1", "idle")

    policy.apply_pet(PetView(generation=4, visible=False, actions=ACTIONS))
    assert scheduler.pending == 0  # 隐藏立刻取消待确认的完成通知

    clock.advance(5.0)
    scheduler.tick()
    assert bubbles_of(out) == []

    before = len(out)
    policy.on_state("a1", "working")  # 隐藏期间不产生建议
    assert len(out) == before

    policy.apply_pet(PetView(generation=5, visible=True, actions=ACTIONS))
    clock.advance(5.0)
    policy.on_state("a1", "sleeping")
    assert actions_of(out)[-1] == SYSTEM_ACTION_IDLE


def test_removed_agent_leaves_no_state_behind() -> None:
    """Agent 被移除后，它的残留状态不得再影响「是否还有其他 Agent 在忙」。"""
    agents = {"a1": _agent("a1", "Claude"), "a2": _agent("a2", "Copilot")}
    policy, out, clock, scheduler = make_policy(agents=agents)
    policy.on_state("a2", "working")

    policy.apply_config(
        ConfigView(revision=8, agent_backend="python", agents={"a1": _agent("a1", "Claude")})
    )
    clock.advance(3.0)
    policy.on_state("a1", "working")
    policy.on_state("a1", "idle")
    clock.advance(0.9)
    scheduler.tick()
    assert SYSTEM_ACTION_IDLE in actions_of(out)


def test_close_cancels_pending_done() -> None:
    policy, out, clock, scheduler = make_policy()
    policy.on_state("a1", "working")
    clock.advance(3.0)
    policy.on_state("a1", "idle")
    policy.close()
    assert scheduler.pending == 0
    clock.advance(1.0)
    scheduler.tick()
    assert bubbles_of(out) == []


def test_proposal_ttl_uses_contract_default() -> None:
    policy, out, _, _ = make_policy()
    policy.on_state("a1", "working")
    assert out[0].ttl_ms == 2000


def test_closed_policy_is_inert() -> None:
    policy, out, clock, scheduler = make_policy()
    policy.close()
    policy.on_state("a1", "working")
    assert out == []


@pytest.mark.parametrize("state", ["idle", "sleeping", "attention", "error"])
def test_non_busy_states_never_rotate_link_action(state: str) -> None:
    policy, out, _, _ = make_policy()
    policy.on_state("a1", state)
    for action_id in actions_of(out):
        assert action_id in {SYSTEM_ACTION_IDLE, SYSTEM_ACTION_BUBBLE}
