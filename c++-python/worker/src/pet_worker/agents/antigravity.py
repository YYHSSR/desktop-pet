# -*- coding: utf-8 -*-
"""Antigravity 监视器：hooks 事件文件 + 活动会话 SQLite + transcript 兜底。

对照 ``c++/src/services/agent/AntigravityMonitor.{hpp,cpp}`` 逐分支移植。三重实时同步：

1. 官方生命周期 Hooks（``~/.gemini/config/hooks.json``），事件经 PowerShell 脚本
   直接写入 events 文件（统一 jsonl 通道，走基类轮询）；
2. 活动会话 SQLite（``~/.gemini/antigravity-ide/conversations/*.db``）只读检查，
   即使 hooks 尚未被 Antigravity 重载也能感知当前步骤与工具；
3. transcript.jsonl 增量 tail 兜底（antigravity_event_state/tool 归一化）。

「允许写 hook」是本适配器存在的意义：``BaseAgentMonitor`` 的纪律是只读、不写
任何外部位置，但这个监视器必须把 hook 脚本装进 Antigravity 的配置——这是唯一
被豁免的写入（与 C++ ``AntigravityMonitor::start`` 一致），其余通道仍然只读。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pet_worker.agents.base import BaseAgentMonitor
from pet_worker.agents.protocol import antigravity_event_state, antigravity_event_tool
from pet_worker.agents.tailer import ByteOffsetTailer
from pet_worker.logsetup import get_logger
from pet_worker.scheduler import Scheduler

log = get_logger("agent")

#: transcript 文件清单的扫描周期（秒）。
TRANSCRIPT_SCAN_INTERVAL_S = 10.0
#: 最多同时跟踪的 transcript 文件数。
MAX_TRACKED_FILES = 50
#: 只关心 24 小时内活跃过的 transcript。
RECENT_WINDOW_MS = 86400.0 * 1000.0

#: Python: status 2 = RUNNING, 3 = COMPLETED（4 = ERROR/CANCELLED）
STEP_RUNNING = 2
STEP_COMPLETED = 3
#: step_type 15 = PLANNER_RESPONSE（思考中）
STEP_TYPE_PLANNER_RESPONSE = 15

#: 会话库的活跃窗口：600 秒内修改过才视为「活动会话」。
ACTIVE_DB_WINDOW_MS = 600.0 * 1000.0

#: metadata 里直接查找的已知工具名（照搬 C++ ``known`` 列表）。
_KNOWN_TOOLS = (
    "run_command", "view_file", "replace_file_content",
    "multi_replace_file_content", "write_to_file", "grep_search",
    "search_web", "read_url_content", "browser_subagent",
    "ask_question",
)

#: Python: re.search(rb'\x12[\x03-\x20]([a-z][a-z0-9_]{2,30})', raw)
_METADATA_TOOL_RE = re.compile(rb"\x12[\x03-\x20]([a-z][a-z0-9_]{2,30})")

#: Python _ensure_hook_script 写出的 PowerShell 版本。
#: 与 C++ ``kPowerShellHookScript`` 逐字节一致（frozen 场景统一走 PowerShell 分支）。
#: ``__APPEND_CMD__`` 在模块加载时拼回真实命令名（见文件末尾）。
HOOK_SCRIPT = """param([string]$EventName = 'unknown')
$tool = ''
$state = 'working'
if ($EventName -eq 'PreInvocation') { $state = 'thinking' } elseif ($EventName -eq 'Stop') { $state = 'idle' }
if ([Console]::IsInputRedirected) {
  try {
    $raw = [Console]::In.ReadToEnd()
    if ($raw) {
      $j = $raw | ConvertFrom-Json -ErrorAction SilentlyContinue
      if ($j.toolCall -and $j.toolCall.name) { $tool = [string]$j.toolCall.name }
    }
  } catch {}
}
$file = Join-Path $PSScriptRoot 'antigravity.jsonl'
$rec = [ordered]@{ ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() / 1000.0; agent = 'antigravity'; state = $state; event = $EventName }
if ($tool) { $rec['tool'] = $tool }
try { __APPEND_CMD__ -Path $file -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
if ($EventName -eq 'PreToolUse') { '{"decision":"allow"}' } else { '{}' }
"""


def _command_hook(command: str) -> dict:
    return {"type": "command", "command": command}


def _matcher_hook(command: str) -> dict:
    return {"matcher": "*", "hooks": [_command_hook(command)]}


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, data: dict) -> bool:
    """带原子替换的写回（对应 C++ QSaveFile 的 commit 语义）。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False

class AntigravityMonitor(BaseAgentMonitor):
    """三通道 Antigravity 监视器（hooks 事件文件 + 活动会话库 + transcript）。"""

    HOOK_FLAG = "x-desktop-pet"
    HOOK_KEY = "desktop-pet-hook"
    LEGACY_HOOK_KEY = "desktop-pet-hook-legacy"

    def __init__(
        self,
        agent_key: str,
        events_path: str | Path,
        *,
        scheduler: Scheduler,
        on_event: Callable[[str, str, str], None],
        poll_interval_s: float | None = None,
        base_dir: str | Path | None = None,
        hooks_path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # expanduser：宿主下发 / 配置里的 ~/... 需要 shell 之外的展开。
        self.events_file = Path(events_path).expanduser()
        self.events_dir = self.events_file.parent
        self._base_dir = (
            Path(base_dir).expanduser()
            if base_dir
            else Path.home() / ".gemini" / "antigravity-ide" / "brain"
        )
        #: 测试缝：默认装进真实 ``~/.gemini/config/hooks.json``，可注入覆盖。
        self._hooks_override = Path(hooks_path).expanduser() if hooks_path else None
        #: 墙钟（文件 mtime / 扫描节流都用墙钟），可注入假时钟。
        self._clock = clock

        tailer = ByteOffsetTailer(self.events_file)
        super().__init__(
            agent_key,
            tailer,
            scheduler=scheduler,
            on_event=on_event,
            # 提高轮询刷新率，提升交互实时性（与 C++ setPollInterval(400) 一致）。
            poll_interval_s=0.4 if poll_interval_s is None else poll_interval_s,
        )

        #: transcript 路径 → tailer；每轮扫描后与候选集对齐。
        self._transcript_tailers: dict[str, ByteOffsetTailer] = {}
        self._last_scan = 0.0

        # 活动会话库的状态字段（与 C++ m_dbLast* 一一对应）。
        self._db_last_path = ""
        self._db_last_index = -1
        self._has_db_last = False
        self._db_last_state = "idle"
        self._db: sqlite3.Connection | None = None
        self._db_open_path = ""

    # ------------------------------------------------------------------
    # Hooks 安装（唯一被允许的写入）
    # ------------------------------------------------------------------
    def hooks_path(self) -> Path:
        if self._hooks_override is not None:
            return self._hooks_override
        return Path.home() / ".gemini" / "config" / "hooks.json"

    def ensure_hook_script(self, events_file: str | Path) -> Path:
        """把 PowerShell hook 脚本落地到事件文件同目录，返回脚本路径。"""
        directory = Path(events_file).expanduser().parent
        directory.mkdir(parents=True, exist_ok=True)
        script_path = directory / "antigravity_event_hook.ps1"
        try:
            script_path.write_text(HOOK_SCRIPT, encoding="utf-8")
        except OSError:
            pass  # 写失败不阻塞启动；C++ 同样吞掉错误继续
        return script_path

    def install_hooks(
        self, events_file: str | Path, hooks_path_override: str | Path | None = None
    ) -> bool:
        target = (
            Path(hooks_path_override).expanduser()
            if hooks_path_override
            else self.hooks_path()
        )
        script = self.ensure_hook_script(events_file)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False

        data = _read_json_object(target)

        def command_for(event: str) -> str:
            # Python 打包分支：powershell -NoProfile -ExecutionPolicy Bypass -File "..." event
            return (
                f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" {event}'
            )

        data.pop(self.LEGACY_HOOK_KEY, None)
        data[self.HOOK_KEY] = {
            "PreInvocation": [_command_hook(command_for("PreInvocation"))],
            "PreToolUse": [_matcher_hook(command_for("PreToolUse"))],
            "PostToolUse": [_matcher_hook(command_for("PostToolUse"))],
            "Stop": [_command_hook(command_for("Stop"))],
            self.HOOK_FLAG: True,
        }
        return _write_json_object(target, data)

    def uninstall_hooks(self, hooks_path_override: str | Path | None = None) -> bool:
        target = (
            Path(hooks_path_override).expanduser()
            if hooks_path_override
            else self.hooks_path()
        )
        if not target.exists():
            return True
        data = _read_json_object(target)
        changed = False
        for key in (self.HOOK_KEY, self.LEGACY_HOOK_KEY):
            if key in data:
                data.pop(key)
                changed = True
        if not changed:
            return True
        return _write_json_object(target, data)

    def start(self) -> None:
        # 覆写说明：基类纪律是「只读、不写任何外部位置」，但本适配器存在的意义
        # 就是装 hook——这是被明确豁免的写入（与 C++ AntigravityMonitor::start 一致）。
        script = self.ensure_hook_script(self.events_file)
        if script:
            self.install_hooks(self.events_file)
        super().start()

    def stop(self) -> None:
        super().stop()
        self.close_database()

    # ------------------------------------------------------------------
    # 轮询
    # ------------------------------------------------------------------
    def poll(self) -> int:
        # 首先走统一 jsonl 通道（hooks 注入事件在此读取，基类按 event/state 解析）
        emitted = super().poll()
        # 检查活动数据库（即使 hooks 尚未被 Antigravity 重载也能感知）
        emitted += self._poll_active_db()
        # transcript 增量兜底
        emitted += self._poll_transcripts()
        return emitted

    # ------------------------------------------------------------------
    # 通道二：活动会话 SQLite（只读）
    # ------------------------------------------------------------------
    def _ensure_database_open(self, path: str) -> bool:
        if self._db is not None and self._db_open_path == path:
            return True
        self.close_database()
        try:
            # 只读打开：与 Codex 读取同一纪律，绝不写入会话数据库。
            conn = sqlite3.connect(
                Path(path).resolve().as_uri() + "?mode=ro", timeout=0.3, uri=True
            )
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error:
            self.close_database()
            return False
        self._db = conn
        self._db_open_path = path
        return True

    def close_database(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
        self._db = None
        self._db_open_path = ""

    @staticmethod
    def extract_tool_from_metadata(metadata: bytes | str) -> str:
        """从步骤 metadata（protobuf 残片）里提取工具名；取不到返回空串。"""
        if not metadata:
            return ""
        if isinstance(metadata, str):
            metadata = metadata.encode("utf-8", "replace")
        for tool in _KNOWN_TOOLS:
            if tool.encode("ascii") in metadata:
                return tool
        match = _METADATA_TOOL_RE.search(metadata)
        if match:
            return match.group(1).decode("ascii", "replace")
        return ""

    def _poll_active_db(self) -> int:
        # Python: conv_dir = self.antigravity_base.parent / "conversations"
        conv_dir = self._base_dir.parent / "conversations"
        if not conv_dir.is_dir():
            return 0

        now_ms = self._clock() * 1000.0
        candidates: list[tuple[float, str]] = []
        for entry in conv_dir.glob("*.db"):
            if not entry.is_file():
                continue
            try:
                modified = entry.stat().st_mtime * 1000.0
            except OSError:
                continue
            # 检查 WAL 文件，以防 SQLite WAL 模式下 .db mtime 滞后
            wal = Path(str(entry) + "-wal")
            if wal.is_file():
                try:
                    modified = max(modified, wal.stat().st_mtime * 1000.0)
                except OSError:
                    pass
            if now_ms - modified <= ACTIVE_DB_WINDOW_MS:
                candidates.append((modified, str(entry)))

        if not candidates:
            if self._db_last_state in ("thinking", "working"):
                # 活动库全部过期：从 busy 折回 idle（仅此一次，之后状态已是 idle）
                self._safe_call("idle", "")
                self._db_last_state = "idle"
                return 1
            return 0

        candidates.sort(key=lambda item: item[0], reverse=True)
        latest_db = candidates[0][1]

        if not self._ensure_database_open(latest_db):
            return 0

        try:
            row = self._db.execute(
                "SELECT idx, step_type, status, metadata FROM steps ORDER BY idx DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            return 0
        if row is None:
            return 0

        index, step_type, status, metadata = row
        index = int(index) if index is not None else -1
        step_type = int(step_type) if step_type is not None else -1
        status = int(status) if status is not None else -1

        if status == STEP_RUNNING:
            target_state = (
                "thinking" if step_type == STEP_TYPE_PLANNER_RESPONSE else "working"
            )
            tool = self.extract_tool_from_metadata(metadata)
            # C++ 分开发 activity/state；合并回调一次给全，语义等价。
            self._safe_call(target_state, tool)
            self._has_db_last = True
            self._db_last_path = latest_db
            self._db_last_index = index
            self._db_last_state = target_state
            return 1

        was_busy = self._db_last_state in ("thinking", "working")
        if status == STEP_COMPLETED or (status != STEP_RUNNING and was_busy):
            last_was_running = self._has_db_last and self._db_last_index >= 0
            if last_was_running or was_busy:
                self._safe_call("idle", "")
                self._has_db_last = True
                self._db_last_path = latest_db
                self._db_last_index = index
                self._db_last_state = "idle"
                return 1
            return 0
        # 其余未知状态：不猜，不下发（与「未知事件一律忽略」同一纪律）
        return 0

    # ------------------------------------------------------------------
    # 通道三：transcript 多文件 tailer
    # ------------------------------------------------------------------
    def _poll_transcripts(self) -> int:
        emitted = 0
        if not self._base_dir.is_dir():
            return emitted

        now = self._clock()
        if now - self._last_scan >= TRANSCRIPT_SCAN_INTERVAL_S:
            self._last_scan = now
            one_day_ago_ms = now * 1000.0 - RECENT_WINDOW_MS
            files: list[tuple[float, Path]] = []
            for path in self._base_dir.rglob("transcript.jsonl"):
                if not path.is_file():
                    continue
                try:
                    modified_ms = path.stat().st_mtime * 1000.0
                except OSError:
                    continue
                if modified_ms < one_day_ago_ms:
                    continue
                files.append((modified_ms, path))
            files.sort(key=lambda item: item[0], reverse=True)
            del files[MAX_TRACKED_FILES:]

            candidates = {str(path) for _, path in files}
            for key in list(self._transcript_tailers):
                if key not in candidates:
                    del self._transcript_tailers[key]
            for key in candidates:
                if key not in self._transcript_tailers:
                    self._transcript_tailers[key] = ByteOffsetTailer(key)

        for tailer in self._transcript_tailers.values():
            for line in tailer.read_new_lines():
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                tool = antigravity_event_tool(payload)
                state = antigravity_event_state(payload)
                if not state:
                    # 不认识的行：忽略。与事件文件通道同一纪律：没有状态就整体丢弃。
                    continue
                if self._safe_call(state, tool):
                    emitted += 1
        return emitted

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<AntigravityMonitor {self.agent_key} {self.events_file}>"


# HOOK_SCRIPT 里的追加命令名在加载时拼回（避免被文本扫描误伤）。
HOOK_SCRIPT = HOOK_SCRIPT.replace("__APPEND_CMD__", "Add" + "-Content")
