# -*- coding: utf-8 -*-
"""状态 → 意图：Worker 侧的**业务规则**。

这是整个混合架构里最"易变"的一层，也是搬进 Python 的主要理由：动作池、台词、
工具关键词、节流阈值、完成确认窗口都在这里，改这些不需要重新编译 C++。

设计边界（对应设计方案 6.1 的分工）：

- 本层只回答「**想做什么**」——给出 ``action_id`` 与气泡文案，输出 ``Proposal``；
- 「**此刻是否允许执行**」完全不在本层——拖拽锁、气泡位抢占、TTL 过期、
  generation 校验都是宿主 ``ActionArbiter`` 的事；
- 因此本层**不做任何本地状态假设**：不看桌宠当前在播什么、不缓存"正在播的动作"。

移植基准是 ``python/pet/services/agent_link.py::AgentLinkManager``，行为逐条对齐：
去抖、节流、动作轮换、busy→idle 完成确认（800ms）、完成冷却（5s）、
过程汇报三重限流（同 Agent 10s / 同文案 60s / 全局 8s）。
差异只有一处且是刻意的：旧实现直接操作窗口（``request_link_anim``），
这里改成产出建议交给宿主仲裁。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pet_worker.contracts import (
    BUSY_STATES,
    DEFAULT_BUBBLE_DURATION_MS,
    DEFAULT_PROPOSAL_TTL_MS,
    SYSTEM_ACTION_BUBBLE,
    SYSTEM_ACTION_IDLE,
)
from pet_worker.logsetup import get_logger
from pet_worker.runtime import AgentConfig, ConfigView, PetView, PolicyConfig
from pet_worker.scheduler import Scheduler, Task

log = get_logger("policy")

#: 过程汇报文案：工具名 → 用户可读短语。不展示原始命令/路径——那既泄露隐私又没人想看。
TOOL_LABELS = {
    "read": "正在读文件", "write": "正在写文件", "edit": "正在改代码",
    "notebookedit": "正在改代码", "bash": "正在跑命令", "shell": "正在跑命令",
    "pwsh": "正在跑命令", "powershell": "正在跑命令",
    "grep": "正在搜索", "glob": "正在搜索", "search": "正在搜索",
    "memory_search": "正在翻记忆",
    "webfetch": "正在查网页", "websearch": "正在查网页",
    "fetch": "正在查网页", "browser": "正在查网页", "web_fetch": "正在查网页",
    "web_search": "正在查网页", "read_page": "正在读网页",
    "task": "正在派活给子代理", "todowrite": "正在列计划",
    "run_command": "正在跑命令", "exec_command": "正在跑命令",
    "view_file": "正在读文件", "list_dir": "正在浏览目录",
    "replace_file_content": "正在改代码", "multi_replace_file_content": "正在改代码",
    "apply_patch": "正在改代码", "write_to_file": "正在写代码",
    "grep_search": "正在搜索", "search_web": "正在查网页",
    "read_url_content": "正在读网页", "browser_subagent": "正在浏览网页",
    "ask_question": "正在准备提问",
    "image_generation": "正在画图", "image_view": "正在看图",
}
UNKNOWN_TOOL_LABEL = "正在调用工具"

#: 工具 → 动作名关键词。命中即挑选动作名包含这些词的动作。
TOOL_ACTION_HINTS = {
    "bash": ("代码", "敲", "工作", "打字"),
    "shell": ("代码", "敲", "工作", "打字"),
    "pwsh": ("代码", "敲", "工作", "打字"),
    "powershell": ("代码", "敲", "工作", "打字"),
    "run_command": ("代码", "敲", "工作", "打字"),
    "exec_command": ("代码", "敲", "工作", "打字"),
    "edit": ("写", "代码", "记录", "打字"),
    "write": ("写", "代码", "记录", "打字"),
    "write_to_file": ("写", "代码", "记录", "打字"),
    "replace_file_content": ("写", "代码", "记录", "打字"),
    "multi_replace_file_content": ("写", "代码", "记录", "打字"),
    "apply_patch": ("写", "代码", "记录", "打字"),
    "read": ("看", "思考", "观察", "记录"),
    "view_file": ("看", "思考", "观察", "记录"),
    "search": ("看", "思考", "观察"),
    "grep": ("看", "思考", "观察"),
    "grep_search": ("看", "思考", "观察"),
    "web_search": ("看", "思考", "观察"),
    "search_web": ("看", "思考", "观察"),
    "browser": ("看", "观察"),
    "browser_subagent": ("看", "观察"),
    "image_generation": ("画", "创作", "记录"),
    "image_view": ("看", "观察"),
}

#: 动作池：主动作严格交替，每第 3 次插播短摸鱼。
LINK_MAIN = ("写代码", "吃Token")
LINK_BREAK = ("轻快记录", "漂浮踏步")
LINK_MAIN_KEYWORDS = ("代码", "工作", "写", "打字", "敲")
LINK_BREAK_KEYWORDS = ("记录", "踏步", "伸懒腰")

#: 气泡时长（毫秒）。过程汇报最短，完成通知最长——它最需要被看见。
BUBBLE_MS_START = 3200
BUBBLE_MS_ACTIVITY = 2600

#: 工具动作的最小间隔（秒）：同 Agent 2 秒内不重复因工具触发动作。
TOOL_ANIM_MIN_INTERVAL_S = 2.0


@dataclass(frozen=True)
class Proposal:
    """一条待宿主仲裁的行为建议。字段与 ``behavior.propose`` 一一对应。"""

    action_id: str = ""
    agent: str = ""
    bubble_text: str = ""
    bubble_important: bool = False
    bubble_duration_ms: int = DEFAULT_BUBBLE_DURATION_MS
    ttl_ms: int = DEFAULT_PROPOSAL_TTL_MS

    def bubble(self) -> dict | None:
        if not self.bubble_text:
            return None
        return {
            "text": self.bubble_text,
            "important": self.bubble_important,
            "duration_ms": self.bubble_duration_ms,
        }


class BehaviorPolicy:
    """把「Agent 状态 / 工具事件」翻译成 ``Proposal``。

    所有时间判断走注入的单调时钟，所有延迟走注入的 ``Scheduler``：
    策略本身没有线程、没有定时器对象，因此可以在单测里用假时钟精确推进。
    """

    def __init__(
        self,
        submit: Callable[[Proposal], None],
        *,
        scheduler: Scheduler,
        clock: Callable[[], float],
    ) -> None:
        #: 建议出口。由装配层接到 ``Runtime.emit_behavior_propose``。
        self._submit = submit
        self._scheduler = scheduler
        self._clock = clock

        self._agents: dict[str, AgentConfig] = {}
        self._policy = PolicyConfig()
        self._actions: tuple[str, ...] = ()
        self._visible = True
        #: 关闭后彻底静默：退出路径上迟到的定时器或事件不该再发出建议。
        self._closed = False

        #: 去抖用：agent → (已生效状态, 时刻)
        self._last_applied: dict[str, tuple[str, float]] = {}
        #: 原始状态流（不受去抖/节流影响）：完成判定必须看它。
        #: 用 _last_applied 判定完成会丢通知——节流恰好会吃掉紧跟其后的 idle。
        self._last_raw: dict[str, str] = {}
        self._done_tasks: dict[str, Task] = {}
        self._done_cooldown: dict[str, float] = {}
        #: busy 周期内出现过 attention / error 的 Agent
        self._saw_alert: set[str] = set()
        self._saw_error: set[str] = set()
        #: 联动动作轮换计数
        self._link_seq = 0
        self._last_activity: dict[str, tuple[str, float]] = {}
        self._activity_global_last = 0.0
        self._last_tool_anim: dict[str, tuple[str, float]] = {}

        self.counters: dict[str, int] = {
            "proposals": 0,
            "debounced": 0,
            "throttled": 0,
            "done_confirmed": 0,
            "done_suppressed": 0,
            "activity_throttled": 0,
            "tool_anim_skipped": 0,
        }

    # ------------------------------------------------------------------
    # 配置与快照
    # ------------------------------------------------------------------
    def apply_config(self, config: ConfigView) -> None:
        """应用配置快照：更新 Agent 列表与策略参数，并清理已移除 Agent 的残留状态。"""
        self._agents = dict(config.agents)
        self._policy = config.policy
        for key in list(self._last_raw):
            if key not in self._agents:
                self._cancel_done(key)
                self._last_raw.pop(key, None)
                self._last_applied.pop(key, None)
                self._done_cooldown.pop(key, None)
                self._last_activity.pop(key, None)
                self._last_tool_anim.pop(key, None)
                self._saw_alert.discard(key)
                self._saw_error.discard(key)

    def apply_pet(self, pet: PetView) -> None:
        """应用宠物快照：动作目录与可见性。

        ``actions`` 是 ``action_id`` 列表（``<folder>/<动作名>``），
        动作名用于与关键词匹配，``action_id`` 原样回给宿主。
        """
        self._actions = pet.actions
        visible = bool(pet.visible)
        if visible != self._visible:
            self._visible = visible
            if not visible:
                self.pause()
            else:
                self.resume()

    def pause(self) -> None:
        """桌宠隐藏或退出中：取消待确认的完成通知。

        不重置 ``_last_raw``——恢复显示后仍知道上次是什么状态，
        否则「隐藏期间 Agent 干完了」会变成一条永远不来的通知。
        """
        for key in list(self._done_tasks):
            self._cancel_done(key)

    def resume(self) -> None:
        """恢复显示。迟到的状态变化会在下一次轮询里重新触发判定。"""

    def close(self) -> None:
        self._closed = True
        for key in list(self._done_tasks):
            self._cancel_done(key)

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------
    def on_state(self, agent_key: str, state: str) -> None:
        """处理一条标准状态。"""
        if self._closed or not self._visible or agent_key not in self._agents:
            return

        now = self._clock()
        prev_raw = self._last_raw.get(agent_key)
        self._last_raw[agent_key] = state

        # --- 完成检测（走原始状态流） ---
        if state in BUSY_STATES:
            self._cancel_done(agent_key)
            self._saw_alert.discard(agent_key)
            if prev_raw != "error":
                self._saw_error.discard(agent_key)
        elif state in ("attention", "error") and prev_raw in BUSY_STATES:
            self._saw_alert.add(agent_key)
            if state == "error":
                self._saw_error.add(agent_key)
            # busy 后的 attention/error 也走完成确认：800ms 内回忙则取消
            # （工具报错后重试是常态，不该立刻报"干完活"）。
            self._schedule_done(agent_key)
        elif state in ("idle", "sleeping") and prev_raw in BUSY_STATES:
            self._schedule_done(agent_key)

        # --- 去抖：同一 Agent 连续相同状态只生效第一次 ---
        last = self._last_applied.get(agent_key)
        if last is not None and last[0] == state:
            self.counters["debounced"] += 1
            return
        # --- 节流：同 Agent 两次动作/气泡切换最小间隔 ---
        if last is not None and (now - last[1]) < self._min_interval_s:
            self.counters["throttled"] += 1
            return
        self._last_applied[agent_key] = (state, now)
        log.debug("Agent 状态生效 [%s]: %s", agent_key, state)

        if state in BUSY_STATES:
            action = self._next_link_action()
            if action:
                self._emit(Proposal(action_id=action, agent=agent_key))
            self._maybe_notify_start(agent_key, prev_raw, state)
        elif state == "attention":
            # busy 后的 attention 由完成确认接管，避免「需要看一眼」与「完成通知」双气泡。
            if prev_raw not in BUSY_STATES:
                self._emit(self._bubble(
                    agent_key, "主人，Agent 这边需要你看一眼～", important=True
                ))
        elif state == "error":
            if prev_raw not in BUSY_STATES:
                self._emit(self._bubble(agent_key, "Agent 执行好像遇到报错了…", important=True))
        elif state in ("sleeping", "idle"):
            self._emit(Proposal(action_id=SYSTEM_ACTION_IDLE, agent=agent_key))

    def on_activity(self, agent_key: str, tool: str) -> None:
        """处理一次调用工具：先按工具挑动作，再（可选）冒过程气泡。"""
        if self._closed or not self._visible or agent_key not in self._agents:
            return
        self._tool_action(agent_key, tool)
        if not self._policy.notify_activity:
            return

        tool_key = str(tool).strip().lower()
        label = TOOL_LABELS.get(tool_key, UNKNOWN_TOOL_LABEL)
        now = self._clock()
        last = self._last_activity.get(agent_key)
        if last is not None:
            # 同一文案 60s 内不重复；同 Agent 两条之间至少 10s。
            if last[0] == label and (now - last[1]) < self._policy.activity_same_label_ms / 1000.0:
                self.counters["activity_throttled"] += 1
                return
            if (now - last[1]) < self._policy.activity_interval_ms / 1000.0:
                self.counters["activity_throttled"] += 1
                return
        # 全局最小间隔：多 Agent 并发时不刷屏。
        if (now - self._activity_global_last) < self._policy.activity_global_min_ms / 1000.0:
            self.counters["activity_throttled"] += 1
            return

        self._last_activity[agent_key] = (label, now)
        self._activity_global_last = now
        self._emit(self._bubble(
            agent_key, f"{self._display(agent_key)} {label}…", duration_ms=BUBBLE_MS_ACTIVITY
        ))

    # ------------------------------------------------------------------
    # 动作池轮换
    # ------------------------------------------------------------------
    def _next_link_action(self) -> str | None:
        """主动作严格交替；每第 3 次插播摸鱼（摸鱼有独立节奏）。"""
        pairs = self._action_pairs()
        if not pairs:
            return None
        main = [item for item in pairs if item[1] in LINK_MAIN]
        brk = [item for item in pairs if item[1] in LINK_BREAK]
        # 不同角色包的动作名不统一：精确名缺失时按语义关键词回退。
        if not main:
            main = [item for item in pairs if any(k in item[1] for k in LINK_MAIN_KEYWORDS)]
        if not brk:
            brk = [item for item in pairs if any(k in item[1] for k in LINK_BREAK_KEYWORDS)]
        # 角色包至少有一个动作时，确保忙碌期间始终有可见反馈。
        if not main and not brk:
            main = pairs
        self._link_seq += 1
        if brk and self._link_seq % 3 == 0:
            return brk[(self._link_seq // 3 - 1) % len(brk)][0]
        if main:
            return main[(self._link_seq - 1) % len(main)][0]
        return brk[(self._link_seq - 1) % len(brk)][0]

    def _tool_action(self, agent_key: str, tool: str) -> None:
        """工具 → 动作。未命中关键词就不动——宁可不播，也别乱播。"""
        now = self._clock()
        last_tool, when = self._last_tool_anim.get(agent_key, ("", 0.0))
        if (now - when) < TOOL_ANIM_MIN_INTERVAL_S:
            self.counters["tool_anim_skipped"] += 1
            return

        tool_key = str(tool).strip().lower()
        words = TOOL_ACTION_HINTS.get(tool_key)
        if words is None:
            if any(k in tool_key for k in ("write", "edit", "patch")):
                words = ("写", "代码", "记录")
            elif any(k in tool_key for k in ("cmd", "exec", "run", "bash", "shell")):
                words = ("代码", "敲", "工作")
            elif any(k in tool_key for k in ("read", "view", "search", "grep", "find")):
                words = ("看", "思考", "观察")
        if words is None:
            return

        candidates = [aid for aid, name in self._action_pairs() if any(w in name for w in words)]
        if not candidates:
            return
        self._last_tool_anim[agent_key] = (tool_key, now)
        self._link_seq += 1
        self._emit(Proposal(
            action_id=candidates[self._link_seq % len(candidates)],
            agent=agent_key,
        ))

    # ------------------------------------------------------------------
    # 完成确认
    # ------------------------------------------------------------------
    def _schedule_done(self, agent_key: str) -> None:
        """800ms 稳定确认：期间回忙则取消（过滤 working→idle→working 抖动）。"""
        self._cancel_done(agent_key)
        self._done_tasks[agent_key] = self._scheduler.after(
            self._policy.done_confirm_ms / 1000.0,
            lambda key=agent_key: self._fire_done(key),
            name=f"policy.done.{agent_key}",
        )

    def _cancel_done(self, agent_key: str) -> None:
        task = self._done_tasks.pop(agent_key, None)
        if task is not None:
            self._scheduler.cancel(task)

    def _fire_done(self, agent_key: str) -> None:
        """确认到期：配置与冷却都在真正要弹之前再查一次。"""
        self._done_tasks.pop(agent_key, None)
        if self._closed or not self._visible:
            return
        if self._last_raw.get(agent_key) in BUSY_STATES:
            return  # 确认期间又忙起来了
        if agent_key not in self._agents:
            return
        if not self._policy.notify_done:
            return
        now = self._clock()
        if (now - self._done_cooldown.get(agent_key, 0.0)) < self._policy.done_cooldown_ms / 1000.0:
            self.counters["done_suppressed"] += 1
            return
        self._done_cooldown[agent_key] = now

        name = self._display(agent_key)
        if agent_key in self._saw_alert:
            # busy 期间出现过 attention/error：不暗示"成功完成"。
            text = f"{name} 那边停了，结果怎么样要主人自己看一眼哦～"
        else:
            text = f"{name} 干完活啦，去看看成果吧～"
        self._saw_alert.discard(agent_key)
        self._saw_error.discard(agent_key)
        self.counters["done_confirmed"] += 1

        # 仅当没有其他 Agent 仍在忙时才回待机，避免 A 完成顶掉 B 的工作动画。
        # 已经因 idle 状态切回待机的就不重复发：那条建议可能被节流吃掉，
        # 但若它已经生效，再来一条只是白跑一次跨进程往返。
        applied = self._last_applied.get(agent_key)
        if applied is not None and applied[0] != "idle":
            if not any(k != agent_key and s in BUSY_STATES for k, s in self._last_raw.items()):
                self._emit(Proposal(action_id=SYSTEM_ACTION_IDLE, agent=agent_key))
                self._last_applied[agent_key] = ("idle", now)
        # 通知与动作分开提交：动作可能因拖拽被拒绝，但"干完活了"这条消息不该跟着丢。
        self._emit(self._bubble(agent_key, text, important=True))

    def _maybe_notify_start(self, agent_key: str, prev_raw: str | None, state: str) -> None:
        """开始干活气泡：只在「非 busy → busy」时提示（thinking↔working 互跳不弹）。"""
        if not self._policy.notify_state:
            return
        if prev_raw in BUSY_STATES:
            return
        if state == "thinking":
            text = self._thinking_text(agent_key)
        else:
            text = f"{self._display(agent_key)} 开始干活啦～随时为你效劳！"
        self._emit(self._bubble(agent_key, text, duration_ms=BUBBLE_MS_START))

    # ------------------------------------------------------------------
    # 文案与工具
    # ------------------------------------------------------------------
    def _thinking_text(self, agent_key: str) -> str:
        """thinking 文案：Agent 自定义 > 默认。``{name}`` 由宿主配置里的占位符替换。"""
        config = self._agents.get(agent_key)
        name = self._display(agent_key)
        custom = (config.thinking_text if config is not None else "").strip()
        if custom:
            return custom.replace("{name}", name)
        return f"{name} 正在深度思考……"

    def _bubble(
        self,
        agent_key: str,
        text: str,
        *,
        important: bool = False,
        duration_ms: int = DEFAULT_BUBBLE_DURATION_MS,
    ) -> Proposal:
        """纯气泡建议：不动当前动作，因此用 ``system/bubble`` 这个哨兵动作。"""
        return Proposal(
            action_id=SYSTEM_ACTION_BUBBLE,
            agent=agent_key,
            bubble_text=text,
            bubble_important=important,
            bubble_duration_ms=duration_ms,
        )

    def _display(self, agent_key: str) -> str:
        config = self._agents.get(agent_key)
        return config.display if config is not None else agent_key

    def _action_pairs(self) -> list[tuple[str, str]]:
        """``action_id`` → ``(action_id, 动作名)``。``<folder>/<动作名>`` 之外的形式按整串当名字。"""
        pairs: list[tuple[str, str]] = []
        for action_id in self._actions:
            name = action_id.split("/", 1)[1] if "/" in action_id else action_id
            pairs.append((action_id, name))
        return pairs

    @property
    def _min_interval_s(self) -> float:
        return self._policy.min_interval_ms / 1000.0

    def _emit(self, proposal: Proposal) -> None:
        self.counters["proposals"] += 1
        try:
            self._submit(proposal)
        except Exception:  # pragma: no cover - 出口异常不该中断事件处理
            log.exception("提交行为建议失败: action=%s", proposal.action_id)

    def health(self) -> dict[str, int]:
        return dict(self.counters)
