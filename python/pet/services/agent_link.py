# -*- coding: utf-8 -*-
"""多 Agent 状态感知与动作联动（Antigravity IDE / ChatGPT / 自定义）。

设计原则（手册 §8）：
1. 绝不使用 mtime 盲轮询；
2. 外部事件采用有界 JSONL 增量读取；ChatGPT 使用独立后台只读本机 Codex 状态适配器；
3. 状态词汇统一：idle / thinking / working / attention / sleeping / error；
4. 状态 -> 桌宠动作映射：
   - thinking -> 写代码 (或 深度思考碎碎念)
   - working -> 原地敲击桌面互动
   - attention -> 气泡提示 ("需要你看一眼～")
   - error -> 气泡提示 ("好像遇到报错了…")
   - sleeping -> 待机
   - idle -> 待机
5. 低功耗：功能默认全关，每个 Agent 独立开关；隐藏时全线 pause()，显示时 resume()；
6. 写入外部配置/hooks 前必须弹窗征得用户明确同意。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from pet.core.agents import AGENT_NAMES
from pet.services.chatgpt_monitor import ChatGPTMonitor

log = logging.getLogger("desktop-pet")

# 标准统一状态词汇
VALID_STATES = {"idle", "thinking", "working", "attention", "sleeping", "error"}

# 通用事件名到统一状态的默认映射
DEFAULT_EVENT_STATE_MAP = {
    # 常用生命周期
    "SessionStart": "idle",
    "SessionEnd": "idle",
    "UserPromptSubmit": "thinking",
    "thinking": "thinking",
    "PreInvocation": "thinking",
    "PostInvocation": "working",
    # 工具与执行
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Stop": "attention",
    "StopFailure": "error",
    "SubagentStop": "attention",
    "error": "error",
    "idle": "idle",
}


def normalize_event_state(event_name: str, explicit_state: str = "") -> str:
    """根据事件名或显式 state 字段规范化为标准状态词汇。

    返回空串表示「不认识的事件，忽略」——绝不把未知事件默认当成 working
    （各 Agent 的 transcript 行类型繁杂，默认 working 会导致过度触发）。
    """
    if explicit_state and explicit_state in VALID_STATES:
        return explicit_state
    return DEFAULT_EVENT_STATE_MAP.get(event_name, "")


def antigravity_event_state(data: dict) -> str:
    """Antigravity IDE transcript / event 数据 → 状态。

    - 显式 state 字段（统一协议通道）优先（如 working / thinking / idle）；
    - type=USER_INPUT 或 role=user / USER_EXPLICIT：用户提问 → thinking；
    - 含有 tool_calls 或 type 为执行/工具相关（且 status 未完成）→ working；
    - type=PLANNER_RESPONSE 且包含 tool_calls：正在执行工具 → working；
    - status=DONE 且无进行中工具，或 type=STEP_FINISH / role=assistant 纯文本回复完成 → idle；
    - status=ERROR / FAILED → error；
    解析失败或无明确状态返回 ""。
    """
    if not isinstance(data, dict):
        return ""
    explicit = str(data.get("state", "") or "")
    if explicit:
        return normalize_event_state("", explicit)
    ev_type = str(data.get("type", "") or data.get("event", "")).upper()
    role = str(data.get("role", "") or data.get("source", "")).upper()
    status = str(data.get("status", "") or "").upper()
    tool_calls = data.get("tool_calls")

    if ev_type == "USER_INPUT" or role in ("USER", "USER_EXPLICIT") or ev_type in ("PREINVOCATION", "THINKING"):
        return "thinking"
    if ev_type in ("PRETOOLUSE", "POSTTOOLUSE", "POSTINVOCATION"):
        return "working"
    if ev_type in ("STOP", "SESSIONEND"):
        return "idle"
    if tool_calls and isinstance(tool_calls, (list, tuple)) and len(tool_calls) > 0:
        return "working"
    if ev_type in ("PLANNER_RESPONSE", "MODEL_RESPONSE", "STEP_START"):
        if status in ("DONE", "FINISHED", "SUCCESS") and not tool_calls:
            return "idle"
        return "working"
    if status in ("DONE", "FINISHED", "SUCCESS"):
        return "idle"
    if status in ("ERROR", "FAILED"):
        return "error"
    return ""


def antigravity_event_tool(data: dict) -> str:
    """从 Antigravity transcript / event 提取正在调用的工具名。取不到返回 ""。"""
    if not isinstance(data, dict):
        return ""
    explicit_tool = str(data.get("tool", "") or "").strip()
    if explicit_tool:
        return explicit_tool
    tool_calls = data.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        first = tool_calls[0]
        if isinstance(first, dict):
            fn = first.get("function")
            if isinstance(fn, dict) and fn.get("name"):
                return str(fn["name"]).strip()
            for k in ("name", "tool_name", "toolAction", "toolSummary", "tool"):
                val = first.get(k)
                if val and isinstance(val, str):
                    return val.strip()
    return ""


class ByteOffsetTailer:
    """有界 Byte-Offset 文件增量行读取器。

    特性：
    - 记录上次读取的 byte offset；
    - 启动时若 offset 为 0 且文件已有内容，执行 backfill 防护（移动到末尾），防止重放历史事件；
    - 文件截断/轮转（当前大小 < offset）时安全重置到头部；
    - 单次读取最大字节数有界（如 64KB），防止大文件卡顿；
    - 零外部依赖，毫秒级读取。
    """

    def __init__(self, file_path: Path | str, max_chunk_bytes: int = 65536) -> None:
        self.file_path = Path(file_path)
        self.offset: int = 0
        self.max_chunk_bytes = max_chunk_bytes
        self._initial_backfill_done = False
        self._partial: bytes = b""  # 跨读取边界的未完成行缓冲（防止半行被丢弃）
        self._discard_until_newline = False  # 超长行丢弃模式：跳到下一个换行再恢复
        self._file_id: tuple[int, ...] | None = None  # 文件身份（Win: ino+ctime_ns / POSIX: dev+ino），识别同路径轮转新文件

    def reset(self) -> None:
        self.offset = 0
        self._initial_backfill_done = False
        self._partial = b""
        self._discard_until_newline = False
        self._file_id = None

    def read_new_lines(self) -> list[str]:
        """读取文件自上次 offset 以来的全部完整新增行。

        半行处理：若读取末尾不是换行符（行被 chunk 截断或写入方尚未写完），
        未完成部分存入 _partial，下次读取时拼回——绝不把半行当整行解析。"""
        if not self.file_path.is_file():
            return []

        try:
            st = self.file_path.stat()
            size = st.st_size
            # 文件身份识别（应对 bridge rename 轮转出同路径新文件）：
            # Windows 用 (ino, ctime_ns)——ctime 是创建时间，追加不变、轮转变化；
            # POSIX 的 ctime 是 inode 变更时间（每次追加都变），只能用 (dev, ino)。
            if os.name == "nt":
                file_id = (st.st_ino, st.st_ctime_ns)
            else:
                file_id = (st.st_dev, st.st_ino)
        except (OSError, AttributeError):
            return []

        # 启动时的首次初始化：若未指定 offset 则跳至当前末尾（backfill 防护）
        if not self._initial_backfill_done:
            self._initial_backfill_done = True
            self.offset = size
            self._file_id = file_id
            self._partial = b""
            return []

        # 文件被截断，或被轮换成同路径的新文件（bridge rename 后新文件可能
        # 在下次轮询前就长到不小于旧 offset，只看 size 会永久跳过新文件前部）
        if size < self.offset or (self._file_id is not None and file_id != self._file_id):
            self.offset = 0
            self._partial = b""
            self._discard_until_newline = False  # 旧文件的超长行丢弃状态不得泄漏进新文件
        self._file_id = file_id

        if size == self.offset:
            return []

        bytes_to_read = min(size - self.offset, self.max_chunk_bytes)
        try:
            with open(self.file_path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(bytes_to_read)
                self.offset = f.tell()
        except OSError as exc:
            log.warning("读取 tail 文件失败 %s: %s", self.file_path, exc)
            return []

        chunk = self._partial + chunk

        # 超长行丢弃模式：上个 chunk 已确认某行超过上限，跳到下一个换行再恢复
        if self._discard_until_newline:
            idx = chunk.find(b"\n")
            if idx == -1:
                return []
            chunk = chunk[idx + 1:]
            self._discard_until_newline = False

        if chunk and not chunk.endswith(b"\n"):
            # 末尾是不完整的半行：留到下次拼接
            idx = chunk.rfind(b"\n")
            if idx == -1:
                self._partial = chunk
                chunk = b""
            else:
                self._partial = chunk[idx + 1:]
                chunk = chunk[: idx + 1]
            # 防呆：单行超过上限时进入丢弃模式（跳过该超长行剩余部分，
            # 避免把它的"后半截"误当成一条新事件解析）
            if len(self._partial) > self.max_chunk_bytes:
                log.warning("tail 行超过 %d 字节上限，丢弃该超长行: %s", self.max_chunk_bytes, self.file_path)
                self._partial = b""
                self._discard_until_newline = True
        else:
            self._partial = b""

        # utf-8-sig：兼容 PowerShell Add-Content -Encoding UTF8 在文件首行写入的 BOM
        text = chunk.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()]


class BaseAgentMonitor(QObject):
    """Agent 监视器抽象基类。"""

    state_changed = Signal(str, str)  # (agent_key, state)
    activity = Signal(str, str)       # (agent_key, 工具名) —— 过程汇报用，仅事件带工具名时发

    def __init__(self, agent_key: str, config_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.agent_key = agent_key
        self.config_dir = Path(config_dir)
        self.events_dir = self.config_dir / "agent-events"
        self.events_file = self.events_dir / f"{agent_key}.jsonl"
        self._running = False
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._poll)
        self._tailer = ByteOffsetTailer(self.events_file)

    def is_running(self) -> bool:
        return self._running and not self._paused

    def start(self) -> None:
        self._running = True
        self._paused = False
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._tailer.reset()
        if not self._timer.isActive():
            self._timer.start()
        log.info("Agent 监视器 [%s] 已启动", self.agent_key)

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._timer.stop()
        log.info("Agent 监视器 [%s] 已停止", self.agent_key)

    def pause(self) -> None:
        if self._running:
            self._paused = True
            self._timer.stop()

    def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            self._timer.start()

    def _poll(self) -> None:
        lines = self._tailer.read_new_lines()
        for line in lines:
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                ev = str(data.get("event", ""))
                st = str(data.get("state", ""))
                tool = str(data.get("tool", "") or "").strip()
                if tool:
                    self.activity.emit(self.agent_key, tool)
                normalized = normalize_event_state(ev, st)
                if not normalized:
                    continue  # 不认识的事件类型：忽略，不误报为 working
                self.state_changed.emit(self.agent_key, normalized)
            except Exception:
                pass


# ----------------------------------------------------------------------
# 各 Agent 具体监视器实现
class AntigravityMonitor(BaseAgentMonitor):
    """Antigravity IDE 监视器。

    支持双重实时同步：
    1. 官方生命周期 Hooks (~/.gemini/config/hooks.json)，事件直接写入 agent-events/antigravity.jsonl；
    2. 活动会话 SQLite 数据库检查（~/.gemini/antigravity-ide/conversations/*.db），实时捕获当前步骤与工具执行；
    3. transcript.jsonl 增量 tail 兜底。
    """

    HOOK_FLAG = "x-desktop-pet"
    HOOK_KEY = "desktop-pet-hook"
    LEGACY_HOOK_KEY = "desktop-pet-hook-legacy"

    def __init__(self, config_dir: Path, parent=None, base_dir: Path | None = None) -> None:
        super().__init__("antigravity", config_dir, parent)
        self.antigravity_base = base_dir or (Path.home() / ".gemini" / "antigravity-ide" / "brain")
        self._tailers: dict[str, ByteOffsetTailer] = {}
        self._scan_interval = 30.0  # 目录发现降频：30s 一次（tail 仍 1.5s）
        self._last_scan = 0.0
        self._db_last: tuple[str, int, int] | None = None
        self._db_last_state: str = "idle"
        self._timer.setInterval(400)  # 提高轮询刷新率，提升交互实时性

    def start(self) -> None:
        try:
            self._ensure_hook_script(self.events_file)
            self.install_hooks(self.events_file)
        except Exception as exc:
            log.debug("初始化 Antigravity hooks 失败: %s", exc)
        super().start()

    @staticmethod
    def get_hooks_path() -> Path:
        return Path.home() / ".gemini" / "config" / "hooks.json"

    @classmethod
    def _ensure_hook_script(cls, events_file: Path) -> tuple[Path, str]:
        """把 Antigravity 事件写入脚本落地到 events_file 同目录，返回 (脚本路径, 命令模板)。"""
        events_file.parent.mkdir(parents=True, exist_ok=True)
        py_script = events_file.parent / "antigravity_event_hook.py"
        py_script.write_text(
            "import json, sys, time\n"
            "from pathlib import Path\n"
            "event = sys.argv[1] if len(sys.argv) > 1 else 'unknown'\n"
            "state_map = {'PreInvocation': 'thinking', 'PreToolUse': 'working', 'PostToolUse': 'working', 'Stop': 'idle'}\n"
            "state = state_map.get(event, 'working')\n"
            "tool = ''\n"
            "try:\n"
            "    if not sys.stdin.isatty():\n"
            "        raw = sys.stdin.read()\n"
            "        if raw.strip():\n"
            "            j = json.loads(raw)\n"
            "            tc = j.get('toolCall')\n"
            "            if isinstance(tc, dict):\n"
            "                tool = str(tc.get('name') or '')\n"
            "except Exception:\n"
            "    pass\n"
            "out = Path(__file__).with_name('antigravity.jsonl')\n"
            "rec = {'ts': time.time(), 'agent': 'antigravity', 'state': state, 'event': event}\n"
            "if tool:\n"
            "    rec['tool'] = tool\n"
            "try:\n"
            "    with out.open('a', encoding='utf-8') as f:\n"
            "        f.write(json.dumps(rec, ensure_ascii=False) + '\\n')\n"
            "except Exception:\n"
            "    pass\n"
            "if event == 'PreToolUse':\n"
            "    print(json.dumps({'decision': 'allow'}))\n"
            "else:\n"
            "    print('{}')\n",
            encoding="utf-8",
        )

        if sys.platform == "win32":
            ps_script = events_file.parent / "antigravity_event_hook.ps1"
            ps_script.write_text(
                "param([string]$EventName = 'unknown')\n"
                "$tool = ''\n"
                "$state = 'working'\n"
                "if ($EventName -eq 'PreInvocation') { $state = 'thinking' } "
                "elseif ($EventName -eq 'Stop') { $state = 'idle' }\n"
                "if ([Console]::IsInputRedirected) {\n"
                "  try {\n"
                "    $raw = [Console]::In.ReadToEnd()\n"
                "    if ($raw) {\n"
                "      $j = $raw | ConvertFrom-Json -ErrorAction SilentlyContinue\n"
                "      if ($j.toolCall -and $j.toolCall.name) { $tool = [string]$j.toolCall.name }\n"
                "    }\n"
                "  } catch {}\n"
                "}\n"
                "$file = Join-Path $PSScriptRoot 'antigravity.jsonl'\n"
                "$rec = [ordered]@{ ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0; agent = 'antigravity'; state = $state; event = $EventName }\n"
                "if ($tool) { $rec['tool'] = $tool }\n"
                "try { Add-Content -Path $file -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}\n"
                "if ($EventName -eq 'PreToolUse') { '{\"decision\":\"allow\"}' } else { '{}' }\n",
                encoding="utf-8",
            )
            if not getattr(sys, "frozen", False) and sys.executable:
                return py_script, f'"{sys.executable}" "{{script}}" {{event}}'
            return ps_script, 'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" {event}'
        else:
            exe = sys.executable if not getattr(sys, "frozen", False) else "python3"
            return py_script, f'"{exe}" "{{script}}" {{event}}'

    @classmethod
    def install_hooks(cls, events_file: Path, hooks_path: Path | None = None) -> bool:
        """向 ~/.gemini/config/hooks.json 写入 Antigravity 官方生命周期 hooks。"""
        hp = hooks_path or cls.get_hooks_path()
        try:
            script, cmd_tmpl = cls._ensure_hook_script(events_file)
            hp.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if hp.is_file():
                try:
                    data = json.loads(hp.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}

            def _cmd(event: str) -> str:
                return cmd_tmpl.replace("{script}", str(script)).replace("{event}", event)

            data.pop(cls.LEGACY_HOOK_KEY, None)
            data[cls.HOOK_KEY] = {
                "PreInvocation": [{"type": "command", "command": _cmd("PreInvocation")}],
                "PreToolUse": [{
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": _cmd("PreToolUse")}],
                }],
                "PostToolUse": [{
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": _cmd("PostToolUse")}],
                }],
                "Stop": [{"type": "command", "command": _cmd("Stop")}],
                cls.HOOK_FLAG: True,
            }

            tmp = hp.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, hp)
            return True
        except Exception as exc:
            log.warning("注入 Antigravity hooks 失败: %s", exc)
            return False

    @classmethod
    def uninstall_hooks(cls, hooks_path: Path | None = None) -> bool:
        """从 ~/.gemini/config/hooks.json 移除本桌宠注入的 hooks。"""
        hp = hooks_path or cls.get_hooks_path()
        if not hp.is_file():
            return True
        try:
            data = json.loads(hp.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return True
            changed = False
            for k in (cls.HOOK_KEY, cls.LEGACY_HOOK_KEY):
                if k in data:
                    del data[k]
                    changed = True
            if changed:
                tmp = hp.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, hp)
            return True
        except Exception as exc:
            log.warning("卸载 Antigravity hooks 失败: %s", exc)
            return False

    @staticmethod
    def _extract_tool_from_metadata(metadata: bytes | str | None) -> str:
        if not metadata:
            return ""
        if isinstance(metadata, str):
            raw = metadata.encode("utf-8", errors="ignore")
        else:
            raw = metadata
        for t in (
            b"run_command", b"view_file", b"replace_file_content",
            b"multi_replace_file_content", b"write_to_file", b"grep_search",
            b"search_web", b"read_url_content", b"browser_subagent",
            b"ask_question",
        ):
            if t in raw:
                return t.decode("ascii")
        import re
        m = re.search(rb'\x12[\x03-\x20]([a-z][a-z0-9_]{2,30})', raw)
        if m:
            try:
                return m.group(1).decode("ascii")
            except Exception:
                pass
        return ""

    def _poll_active_db(self) -> None:
        """检查 Antigravity 活动会话 SQLite 数据库（实时捕获正在运行的步骤）。"""
        try:
            conv_dir = self.antigravity_base.parent / "conversations"
            if not conv_dir.is_dir():
                return
            now = time.time()
            candidates = []
            for p in conv_dir.glob("*.db"):
                try:
                    mtime = p.stat().st_mtime
                    # 检查 WAL 文件，以防 SQLite WAL 模式下 .db mtime 滞后
                    wal_p = p.with_name(p.name + "-wal")
                    if wal_p.is_file():
                        try:
                            mtime = max(mtime, wal_p.stat().st_mtime)
                        except OSError:
                            pass
                    if now - mtime <= 600:
                        candidates.append((mtime, p))
                except OSError:
                    continue
            if not candidates:
                if self._db_last_state in ("thinking", "working"):
                    self.state_changed.emit("antigravity", "idle")
                    self._db_last_state = "idle"
                return
            candidates.sort(key=lambda x: x[0], reverse=True)
            latest_db = candidates[0][1]

            import sqlite3
            con = sqlite3.connect(f"file:{latest_db}?mode=ro", uri=True, timeout=0.3)
            try:
                cur = con.cursor()
                row = cur.execute(
                    "SELECT idx, step_type, status, metadata FROM steps ORDER BY idx DESC LIMIT 1"
                ).fetchone()
            finally:
                con.close()

            if not row:
                return
            idx, step_type, status, metadata = row
            # status: 2 = RUNNING, 3 = COMPLETED, 4 = ERROR/CANCELLED
            # step_type: 15 = PLANNER_RESPONSE (思考中)
            if status == 2:
                target_state = "thinking" if step_type == 15 else "working"
                tool = self._extract_tool_from_metadata(metadata)
                if tool:
                    self.activity.emit("antigravity", tool)
                self.state_changed.emit("antigravity", target_state)
                self._db_last = (str(latest_db), idx, 2)
                self._db_last_state = target_state
            elif status == 3 or (status != 2 and self._db_last_state in ("thinking", "working")):
                if self._db_last is not None and (self._db_last[2] == 2 or self._db_last_state in ("thinking", "working")):
                    self.state_changed.emit("antigravity", "idle")
                    self._db_last = (str(latest_db), idx, 3)
                    self._db_last_state = "idle"
        except Exception as exc:
            log.debug("Antigravity DB 轮询异常: %s", exc)

    def _poll(self) -> None:
        # 首先检查统一 jsonl 通道（hooks 注入事件在此读取）
        super()._poll()

        # 检查活动数据库（即使 hooks 尚未被 Antigravity 重载也能感知）
        self._poll_active_db()

        if not self.antigravity_base.is_dir():
            return

        now = time.time()
        if now - self._last_scan >= self._scan_interval:
            self._last_scan = now
            try:
                one_day_ago = now - 86400
                files = []
                for p in self.antigravity_base.glob("**/transcript.jsonl"):
                    try:
                        if p.stat().st_mtime >= one_day_ago:
                            files.append(p)
                    except OSError:
                        pass
                files = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:50]
                candidates = {str(f) for f in files}
                for stale in [k for k in self._tailers if k not in candidates]:
                    del self._tailers[stale]
                for fkey in candidates:
                    if fkey not in self._tailers:
                        self._tailers[fkey] = ByteOffsetTailer(fkey)
            except Exception as exc:
                log.debug("Antigravity monitor 扫描异常: %s", exc)

        for tailer in self._tailers.values():
            for line in tailer.read_new_lines():
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    tool = antigravity_event_tool(data)
                    if tool:
                        self.activity.emit("antigravity", tool)
                    norm = antigravity_event_state(data)
                    if not norm:
                        continue
                    self.state_changed.emit("antigravity", norm)
                except Exception:
                    pass


class CustomAgentMonitor(BaseAgentMonitor):
    """自定义联动 Agent 监视器（agent_link.custom_agents 配置驱动）。

    只读监听用户指定路径的统一协议 JSONL 事件文件（docs/AGENT_LINK_PROTOCOL.md §4）：
    不创建目录、不写任何外部位置、无需授权弹窗；文件不存在时静默空转等待，
    出现后自动开始增量读取（backfill 防护跳过历史内容）。"""

    def __init__(self, agent_key: str, config_dir: Path, events_path: str, parent=None) -> None:
        super().__init__(agent_key, config_dir, parent)
        self.events_file = Path(events_path).expanduser()
        self.events_dir = self.events_file.parent
        self._tailer = ByteOffsetTailer(self.events_file)

    def start(self) -> None:
        # 覆写基类 start：基类会 mkdir 事件目录，这里只读监听外部文件，
        # 不替用户在任意路径创建目录
        self._running = True
        self._paused = False
        self._tailer.reset()
        if not self._timer.isActive():
            self._timer.start()
        log.info("Agent 监视器 [%s] 已启动 (%s)", self.agent_key, self.events_file)


# ----------------------------------------------------------------------
# Agent 联动总调度管理器
# ----------------------------------------------------------------------

class AgentLinkManager(QObject):
    """多 Agent 联动总调度管理器。

    挂载于 PetWindow，持有已配置的 Agent 监视器，并根据状态驱动桌宠动作与气泡。
    """

    # 联动气泡展示名
    AGENT_NAMES = AGENT_NAMES
    # 过程汇报：工具名 → 用户可读文案（不展示原始命令/路径，带生动 Emoji）
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
        # Antigravity IDE 工具映射
        "run_command": "正在跑命令",
        "view_file": "正在读文件",
        "replace_file_content": "正在改代码",
        "multi_replace_file_content": "正在改代码",
        "write_to_file": "正在写代码",
        "grep_search": "正在搜索",
        "search_web": "正在查网页",
        "read_url_content": "正在读网页",
        "browser_subagent": "正在浏览网页",
        "ask_question": "正在准备提问",
        "image_generation": "正在画图", "image_view": "正在看图",
        "exec_command": "正在跑命令", "apply_patch": "正在改代码",
        "list_dir": "正在浏览目录",
    }
    _UNKNOWN_TOOL_LABEL = "正在调用工具"
    _ACTIVITY_MIN_INTERVAL = 10.0    # 同 Agent 过程气泡最小间隔
    _ACTIVITY_GLOBAL_MIN = 8.0       # 全局最小间隔（多 Agent 并发防刷屏）
    _ACTIVITY_SAME_LABEL = 60.0      # 同一工具文案 60s 内不重复
    _BUSY_STATES = ("working", "thinking")
    _DONE_CONFIRM_MS = 800   # busy→idle 稳定确认窗口（过滤 working→idle→working 抖动）
    _DONE_COOLDOWN_S = 5.0   # 同 Agent 完成气泡最小间隔（最后一道保险）

    def __init__(self, window: Any, config: Any, *, min_interval: float = 2.0,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(window if hasattr(window, "winId") else None)
        self.win = window
        self.cfg = config
        self.config_dir = config.dir
        # 状态节流：同一 Agent 相同状态去抖；同 Agent 两次动作切换最小间隔
        # （transcript 密集写入时防止动画"抽搐"）
        self._min_interval = float(min_interval)
        self._clock = clock
        self._last_applied: dict[str, tuple[str, float]] = {}
        # 原始状态流（不受去抖/节流影响）：用于 busy→idle 完成检测。
        # 不能用 _last_applied 做完成判定——节流会丢掉紧跟其后的 idle，导致完成通知丢失。
        self._last_raw: dict[str, str] = {}
        self._done_pending: dict[str, QTimer] = {}   # agent → 稳定确认定时器
        self._done_timers: dict[str, QTimer] = {}    # 每个 Agent 复用一个，避免回调期间销毁 QObject
        self._done_cooldown: dict[str, float] = {}   # agent → 上次完成气泡时刻
        self._saw_alert: set[str] = set()            # busy 周期内出现过 attention/error 的 Agent
        self._saw_error: set[str] = set()            # busy 周期内真正出现过 error 的 Agent
        self._link_seq = 0                           # 联动动作轮换计数
        # 过程汇报气泡：agent → (上次文案, 时刻)；全局最后一条时刻
        self._last_activity: dict[str, tuple[str, float]] = {}
        self._activity_global_last = 0.0

        self.monitors: dict[str, BaseAgentMonitor] = {
            "antigravity": AntigravityMonitor(self.config_dir, self),
            "chatgpt": ChatGPTMonitor(self.config_dir, self),
        }
        # 自定义联动 Agent：配置驱动的只读监视器（key/path 已在 config 清洗时
        # 保证合法唯一）；显示名合并进实例级 agent_names，类级 AGENT_NAMES
        # 保持仅内置（modern_settings_dialog 等按内置枚举处不受影响）。
        # 注意：运行中新增/修改 custom_agents 需重启桌宠生效。
        self.agent_names: dict[str, str] = dict(self.AGENT_NAMES)
        for item in (self.cfg.get("agent_link", {}).get("custom_agents") or []):
            key = str(item.get("key") or "")
            if not key or key in self.monitors:
                continue
            self.monitors[key] = CustomAgentMonitor(
                key, self.config_dir, str(item.get("path") or ""), self,
            )
            self.agent_names[key] = str(item.get("name") or key)

        chatgpt = self.monitors["chatgpt"]
        chatgpt.task_changed.connect(self._reset_chatgpt_task)
        chatgpt.snapshot_changed.connect(self._on_chatgpt_snapshot)
        self._chatgpt_last_tool_anim = ("", 0.0)
        self._last_tool_anim: dict[str, tuple[str, float]] = {}
        self._link_bubble_busy_until = 0.0
        for mon in self.monitors.values():
            mon.state_changed.connect(self._on_agent_state)
            mon.activity.connect(self._on_agent_activity)
        # 联动动作链：一次性动作播完后若仍有 Agent 在忙，由 window 回调取下一个动作
        if hasattr(self.win, "_pending_link_anim"):
            self.win._link_next_provider = self._next_busy_anim

        self.apply_config()

    def apply_config(self) -> None:
        """根据配置启停各个 Agent 监视器。

        注意用 _running（生命周期状态）而非 is_running()（会被 pause 置 False）——
        否则"隐藏期间关配置"不会真正 stop，恢复显示时又会被 resume 拉起。"""
        agent_cfg = self.cfg.get("agent_link", {})
        for key, monitor in self.monitors.items():
            should_run = bool(agent_cfg.get(key, False))
            if should_run and not monitor._running:
                monitor.start()
            elif not should_run and monitor._running:
                monitor.stop()

    def _reset_chatgpt_task(self):
        was_active = "chatgpt" in self._last_raw
        self._cancel_done_check("chatgpt")
        self._last_raw.pop("chatgpt", None)
        self._last_applied.pop("chatgpt", None)
        self._saw_alert.discard("chatgpt")
        self._saw_error.discard("chatgpt")
        self._done_cooldown.pop("chatgpt", None)
        if was_active and hasattr(self.win, "request_link_idle") and not any(s in self._BUSY_STATES for s in self._last_raw.values()):
            self.win.request_link_idle()

    def _on_chatgpt_snapshot(self, snapshot):
        # Expose a small status object to the menu; no user messages are retained.
        self.chatgpt_snapshot = snapshot

    def _animate_agent_tool(self, agent_key: str, tool: str) -> None:
        """根据 Agent 执行的工具调度桌宠动作（写代码/敲键盘/查阅等）。"""
        now = self._clock()
        last_tool, when = self._last_tool_anim.get(agent_key, ("", 0.0))
        if now - when < 2.0:
            return
        t = str(tool).strip().lower()
        tool_hints = {
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
        words = tool_hints.get(t)
        if words is None:
            if any(k in t for k in ("write", "edit", "patch")):
                words = ("写", "代码", "记录")
            elif any(k in t for k in ("cmd", "exec", "run", "bash", "shell")):
                words = ("代码", "敲", "工作")
            elif any(k in t for k in ("read", "view", "search", "grep", "find")):
                words = ("看", "思考", "观察")
        if words is None:
            return
        acts = list(getattr(self.win, "cats", {}).get("acts", []) or [])
        candidates = [name for name in acts if any(word in name for word in words)]
        if candidates and hasattr(self.win, "request_link_anim"):
            self._last_tool_anim[agent_key] = (t, now)
            if agent_key == "chatgpt":
                self._chatgpt_last_tool_anim = (t, now)
            self._link_seq += 1
            self.win.request_link_anim(candidates[self._link_seq % len(candidates)])

    def _animate_chatgpt_tool(self, tool: str) -> None:
        self._animate_agent_tool("chatgpt", tool)

    def stop(self):
        for monitor in self.monitors.values():
            monitor.stop()
        for key in list(self._done_pending):
            self._cancel_done_check(key)

    def _warn_if_agent_absent(self, agent_key: str) -> None:
        """开启了联动但本机没装对应 Agent 时给用户提示（不然勾了永远没反应）。"""
        # 自定义 Agent：事件文件尚未出现时提示路径，避免"勾了没反应"的困惑
        mon = self.monitors.get(agent_key)
        if isinstance(mon, CustomAgentMonitor):
            if mon.events_file.exists() or not hasattr(self.win, "show_bubble"):
                return
            self.win.show_bubble(
                f"已开启 {self.agent_names.get(agent_key, agent_key)} 联动监听，"
                f"但事件文件还没出现——{mon.events_file} 有事件我才能感知到哦",
                duration_ms=6000,
            )
            return
        hints = {
            "antigravity": ("Antigravity IDE", Path.home() / ".gemini" / "antigravity-ide"),
        }
        item = hints.get(agent_key)
        if not item:
            return
        name, marker = item
        if not marker.exists() and hasattr(self.win, "show_bubble"):
            self.win.show_bubble(
                f"已开启 {name} 联动监听，但没检测到本机安装 {name}——装了它我才能感知到哦",
                duration_ms=6000,
            )

    def set_enabled(self, agent_key: str, enabled: bool) -> bool:
        """开启或关闭指定 Agent 监视器（必要时弹出确认框）。

        返回 False 表示未生效（用户拒绝授权 / hooks 安装失败），调用方应回滚 UI 勾选态。"""
        if agent_key not in self.monitors:
            return False

        if enabled:
            if agent_key == "antigravity":
                AntigravityMonitor.install_hooks(self.monitors["antigravity"].events_file)
        else:
            # 关闭联动时移除我们注入的内容（只删自己的，用户自有配置不碰）
            if agent_key == "antigravity":
                AntigravityMonitor.uninstall_hooks()

        ag_cfg = dict(self.cfg.get("agent_link", {}))
        ag_cfg[agent_key] = bool(enabled)
        self.cfg.set("agent_link", ag_cfg)
        self.cfg.save()
        self.apply_config()
        if agent_key == "chatgpt" and not enabled:
            self._reset_chatgpt_task()
        if enabled:
            self._warn_if_agent_absent(agent_key)
        return True

    def pause(self) -> None:
        """桌宠隐藏时暂停所有监视器，丢弃待播联动动作，并取消所有完成确认计时器
        （否则隐藏期间计时器到期会在隐藏窗口上切动画/弹气泡）。"""
        for mon in self.monitors.values():
            mon.pause()
        if hasattr(self.win, "_pending_link_anim"):
            self.win._pending_link_anim = None
        for key in list(self._done_pending):
            self._cancel_done_check(key)

    def resume(self) -> None:
        """桌宠恢复显示时恢复活动的监视器。"""
        for mon in self.monitors.values():
            mon.resume()

    def _on_agent_state(self, agent_key: str, state: str) -> None:
        """接收 Agent 状态变更并调度桌宠动作/气泡（带去抖与节流）。"""
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return

        now = self._clock()
        if agent_key == "chatgpt" and state in {"attention", "error", "interrupted", "unavailable"}:
            previous = self._last_raw.get(agent_key)
            self._last_raw[agent_key] = state
            self._last_applied[agent_key] = (state, now)
            self._cancel_done_check(agent_key)
            if previous == state:
                return
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()
            messages = {
                "attention": "ChatGPT 正在等你确认或回答，去看一眼吧～",
                "error": "ChatGPT 这次任务失败了，去看看错误信息吧。",
                "interrupted": "ChatGPT 任务已中断，我先陪你休息一下。",
                "unavailable": "暂时读不到 ChatGPT 本机任务状态，正在等待连接。",
            }
            self._show_link_bubble(messages[state], important=state in {"attention", "error"})
            return
        if agent_key == "chatgpt" and state == "idle" and self._last_raw.get(agent_key) == "attention":
            self._schedule_done_check(agent_key)
        # --- 原始状态流（绕开去抖/节流）：busy→idle 完成检测 ---
        # 不能用 _last_applied 判定完成——节流会丢掉紧跟的 idle，导致完成通知丢失。
        prev_raw = self._last_raw.get(agent_key)
        self._last_raw[agent_key] = state
        if state in self._BUSY_STATES:
            self._cancel_done_check(agent_key)
            self._saw_alert.discard(agent_key)
            if prev_raw != "error":
                self._saw_error.discard(agent_key)
        elif state in ("attention", "error") and prev_raw in self._BUSY_STATES:
            self._saw_alert.add(agent_key)
            if state == "error":
                self._saw_error.add(agent_key)
            # busy 后的 attention/error 进入完成确认（800ms 内回忙则取消——例如
            # 工具报错后重试）。
            self._schedule_done_check(agent_key)
        elif state in ("idle", "sleeping") and prev_raw in self._BUSY_STATES:
            # working/thinking → idle：疑似任务完成，800ms 稳定确认
            # （过滤 working→idle→working 抖动；确认期间回忙则取消）
            self._schedule_done_check(agent_key)

        # 去抖：同一 Agent 连续相同状态只生效第一次
        last = self._last_applied.get(agent_key)
        if last is not None and last[0] == state:
            return
        # 节流：同一 Agent 两次动作/气泡切换最小间隔
        if last is not None and (now - last[1]) < self._min_interval and agent_key != "chatgpt":
            return
        self._last_applied[agent_key] = (state, now)

        log.debug("Agent 状态变更 [%s]: %s", agent_key, state)

        # 状态 -> 桌宠行为映射（手册 §8.2）
        if state in ("thinking", "working"):
            # busy 动作池轮换（写代码/吃Token 为主，每第 3 次插播短摸鱼），
            # 经 request_link_anim 平滑衔接：正在播的一次性动作不被打断
            anim = self._next_link_anim_rotation()
            if anim and hasattr(self.win, "request_link_anim"):
                self.win.request_link_anim(anim)
            self._maybe_notify_start(agent_key, prev_raw, state)
        elif state == "attention":
            # busy 后的 attention 由完成确认流程接管，
            # 避免「需要看一眼」和「完成通知」双气泡；独立出现的才立即提醒
            if prev_raw not in self._BUSY_STATES:
                self._show_link_bubble("主人，Agent 这边需要你看一眼～", important=True)
        elif state == "error":
            if prev_raw not in self._BUSY_STATES:
                self._show_link_bubble("Agent 执行好像遇到报错了…", important=True)
        elif state in ("sleeping", "idle"):
            # 回到待机：一次性动作播完自然回，待机/移动中立即回
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()
            elif hasattr(self.win, "idles") and hasattr(self.win, "_pick") and self.win.idles:
                self.win._switch(self.win._pick(self.win.idles))

    # ------------------------------------------------------------------
    # 联动动作池（写代码/吃Token 交替为主，每第 3 次插播短摸鱼）
    # ------------------------------------------------------------------
    _LINK_MAIN = ("写代码", "吃Token")
    _LINK_BREAK = ("轻快记录", "漂浮踏步")
    _LINK_MAIN_KEYWORDS = ("代码", "工作", "写", "打字", "敲")
    _LINK_BREAK_KEYWORDS = ("记录", "踏步", "伸懒腰")

    def _next_link_anim_rotation(self) -> str | None:
        """下一个联动动作：主动作严格交替；每第 3 次插播摸鱼（独立节奏）。"""
        acts = list(getattr(self.win, "cats", {}).get("acts", []) or [])
        main = [a for a in self._LINK_MAIN if a in acts]
        brk = [a for a in self._LINK_BREAK if a in acts]
        # 不同角色包的动作名不统一：精确名缺失时按语义关键词回退。
        if not main:
            main = [a for a in acts if any(k in a for k in self._LINK_MAIN_KEYWORDS)]
        if not brk:
            brk = [a for a in acts if any(k in a for k in self._LINK_BREAK_KEYWORDS)]
        # 角色包至少有一个动作时，确保 Agent 忙碌期间始终有可见反馈。
        if not main and not brk:
            main = acts
        if not main and not brk:
            return None
        self._link_seq += 1
        if brk and self._link_seq % 3 == 0:
            return brk[(self._link_seq // 3 - 1) % len(brk)]
        if main:
            return main[(self._link_seq - 1) % len(main)]
        return brk[(self._link_seq - 1) % len(brk)]

    def _next_busy_anim(self) -> str | None:
        """window 动画结束回调用：仍有 Agent 在忙 → 下一个联动动作；否则 None。
        全员空闲时重置轮换计数——下一个任务从「写代码」重新开始。"""
        if any(s in self._BUSY_STATES for s in self._last_raw.values()):
            return self._next_link_anim_rotation()
        self._link_seq = 0
        return None

    # 进程名 → Agent：该 Agent 联动开启且正忙时的窗口识别。
    # antigravity/chatgpt 有独立桌面进程按进程名识别；另有按窗口标题识别。
    AGENT_PROCESS_HINTS = {
        "antigravity": ("antigravity.exe", "antigravity-ide.exe"),
        "chatgpt": ("codex.exe", "chatgpt.exe"),
    }
    AGENT_TITLE_HINTS = {
        "antigravity": ("antigravity",),
    }

    def busy_agent_owns_process(self, process_name: str, title: str = "") -> bool:
        """前台窗口是否属于「联动开启且正在忙」的 Agent（进程名或窗口标题命中）。"""
        agent_cfg = self.cfg.get("agent_link", {})
        p = str(process_name or "").lower()
        t = str(title or "").lower()
        for agent_key, procs in self.AGENT_PROCESS_HINTS.items():
            if p and p in procs and agent_cfg.get(agent_key) \
                    and self._last_raw.get(agent_key) in self._BUSY_STATES:
                return True
        for agent_key, needles in self.AGENT_TITLE_HINTS.items():
            if t and any(n in t for n in needles) and agent_cfg.get(agent_key) \
                    and self._last_raw.get(agent_key) in self._BUSY_STATES:
                return True
        return False

    # ------------------------------------------------------------------
    # 联动气泡（开始干活可选 / 任务完成通知）
    # ------------------------------------------------------------------
    # 各 Agent 的默认 thinking 文案
    _THINKING_DEFAULTS = {
        "antigravity": "Antigravity 正在深度思考……",
        "chatgpt": "ChatGPT 正在认真思考，我陪你等～",
    }

    def _thinking_text(self, agent_key: str) -> str:
        """thinking 气泡文案：按 Agent 自定义 > 旧全局自定义 > 按 Agent 默认。"""
        agent_cfg = self.cfg.get("agent_link", {})
        custom = (agent_cfg.get("thinking_texts") or {}).get(agent_key, "").strip()
        # 兼容旧的全局 thinking_text 字段（设置页保存时已自动迁移）
        if not custom:
            custom = str(agent_cfg.get("thinking_text", "") or "").strip()
        if custom:
            name = self.agent_names.get(agent_key, agent_key)
            return custom.replace("{name}", name)
        if agent_key in self._THINKING_DEFAULTS:
            return self._THINKING_DEFAULTS[agent_key]
        name = self.agent_names.get(agent_key, agent_key)
        return f"{name} 正在深度思考……"

    def _maybe_notify_start(self, agent_key: str, prev_raw: str | None, state: str = "working") -> None:
        """开始干活气泡：仅「非 busy → busy」时提示（thinking↔working 互跳不弹）。
        低优先级：气泡位被占时直接丢弃。thinking 状态用更有趣的文案。"""
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_state", False):
            return
        if prev_raw in self._BUSY_STATES:
            return
        name = self.agent_names.get(agent_key, agent_key)
        if state == "thinking":
            self._show_link_bubble(self._thinking_text(agent_key), important=False, duration_ms=3200)
        else:
            self._show_link_bubble(f"{name} 开始干活啦～随时为你效劳！", important=False, duration_ms=3200)

    def _on_agent_activity(self, agent_key: str, tool: str) -> None:
        """过程汇报气泡（可选，默认关）：「Antigravity 正在读文件…」这类。
        白名单工具映射 + 三重限流（同 Agent 10s / 同文案 60s / 全局 8s）。"""
        agent_cfg = self.cfg.get("agent_link", {})
        self._animate_agent_tool(agent_key, tool)
        if not agent_cfg.get("notify_activity", False):
            return
        label = self.TOOL_LABELS.get(str(tool).strip().lower(), self._UNKNOWN_TOOL_LABEL)
        now = self._clock()
        last = self._last_activity.get(agent_key)
        min_interval = float(agent_cfg.get("activity_interval", self._ACTIVITY_MIN_INTERVAL))
        global_min = float(agent_cfg.get("activity_global_min", self._ACTIVITY_GLOBAL_MIN))
        same_label_interval = float(agent_cfg.get("activity_same_label", self._ACTIVITY_SAME_LABEL))
        if last is not None:
            if last[0] == label and now - last[1] < same_label_interval:
                return
            if now - last[1] < min_interval:
                return
        if now - self._activity_global_last < global_min:
            return
        self._last_activity[agent_key] = (label, now)
        self._activity_global_last = now
        name = self.agent_names.get(agent_key, agent_key)
        # 过程气泡允许平滑刷新
        self._show_link_bubble(f"{name} {label}…", important=False, duration_ms=2600)

    def _schedule_done_check(self, agent_key: str) -> None:
        self._cancel_done_check(agent_key)
        timer = self._done_timers.get(agent_key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self._DONE_CONFIRM_MS)
            timer.timeout.connect(lambda k=agent_key: self._fire_done(k))
            self._done_timers[agent_key] = timer
        self._done_pending[agent_key] = timer
        timer.start()

    def _cancel_done_check(self, agent_key: str) -> None:
        timer = self._done_pending.pop(agent_key, None)
        if timer is not None:
            timer.stop()

    def _fire_done(self, agent_key: str) -> None:
        """800ms 稳定确认到期：期间回忙则不算完成；配置/冷却在弹出前再查。"""
        timer = self._done_pending.pop(agent_key, None)
        if timer is not None:
            timer.stop()
        if not hasattr(self.win, "isVisible") or not self.win.isVisible():
            return  # 隐藏中不弹不切（pause 已取消计时器，这里是兜底）
        if self._last_raw.get(agent_key) in self._BUSY_STATES:
            return
        agent_cfg = self.cfg.get("agent_link", {})
        if not agent_cfg.get("notify_done", True):
            return
        now = self._clock()
        if now - self._done_cooldown.get(agent_key, 0.0) < self._DONE_COOLDOWN_S:
            return
        self._done_cooldown[agent_key] = now
        name = self.agent_names.get(agent_key, agent_key)
        if agent_key in self._saw_alert:
            # busy 期间出现过 attention/error：不暗示"成功完成"
            text = f"{name} 那边停了，结果怎么样要主人自己看一眼哦～"
        else:
            text = f"{name} 干完活啦，去看看成果吧～"
        self._saw_alert.discard(agent_key)
        self._saw_error.discard(agent_key)
        # 仅当没有其他 Agent 仍在忙时恢复（避免 A 完成顶掉 B 的工作动画）。
        # 必须走 request_link_idle（它会清 _link_anim_current 并尊重一次性动作），
        # 不能裸 _switch——否则残留的 link 状态会把以后的普通同名动作劫持进联动链。
        if not any(k != agent_key and s in self._BUSY_STATES
                   for k, s in self._last_raw.items()):
            if hasattr(self.win, "request_link_idle"):
                self.win.request_link_idle()
            elif hasattr(self.win, "idles") and hasattr(self.win, "_pick") and self.win.idles \
                    and hasattr(self.win, "_switch"):
                self.win._switch(self.win._pick(self.win.idles))
            self._last_applied[agent_key] = ("idle", now)
        self._show_link_bubble(text, important=True)

    def _show_link_bubble(self, text: str, *, important: bool, duration_ms: int = 4500,
                          _retried: int = 0) -> None:
        """联动气泡：不顶掉正在占用气泡位的重要气泡（attention 等）。
        若是前一条联动气泡自身的占位，则允许平滑实时刷新（Ticker 效果）；
        普通气泡遇外部重要气泡直接让路丢弃；外部重要气泡每 2.5s 重试至多 4 次。"""
        if not hasattr(self.win, "show_bubble"):
            return
        busy_until = getattr(self.win, "_bubble_busy_until", 0.0)
        now = time.time()
        link_busy_until = getattr(self, "_link_bubble_busy_until", 0.0)
        is_external_busy = (now < busy_until) and (busy_until > link_busy_until + 0.5)

        if is_external_busy:
            if not important or _retried >= 4:
                return
            QTimer.singleShot(2500, self,
                              lambda t=text, n=_retried: self._show_link_bubble(
                                  t, important=True, _retried=n + 1))
            return
        self.win.show_bubble(text, duration_ms=duration_ms)
        self._link_bubble_busy_until = getattr(self.win, "_bubble_busy_until", now + duration_ms / 1000.0 + 2.0)


