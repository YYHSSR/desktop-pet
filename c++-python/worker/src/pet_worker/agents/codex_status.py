# -*- coding: utf-8 -*-
"""本机 Codex 状态只读适配器。

对照 ``c++/src/services/agent/CodexStatusReader.{hpp,cpp}``（其自身 1:1 移植自
``pet/infrastructure/codex_status.py``）逐分支移植，状态机一条不多、一条不少。

这是版本容忍的本地适配层，不是公开 OpenAI API：不支持的 schema 一律返回
``unavailable``。只有生命周期与工具元数据会离开该模块（不读取对话正文 /
命令参数 / 工具输出 / 推理内容）。

只读纪律（与 C++ 的 ``ScopedReadOnlyDb`` 等价）：
- 以 ``file:...?mode=ro`` URI 打开，``timeout=0.04``（对应 QSQLITE_BUSY_TIMEOUT=40ms），
  打开后立刻 ``PRAGMA query_only=ON``——三重保险，绝不写入；
- 连接用完即关，绝不常驻、绝不创建/索引/迁移 Codex 数据库。
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = ["CodexSnapshot", "CodexStatusReader"]


@dataclass(frozen=True)
class CodexSnapshot:
    """一次只读扫描得到的任务快照；字段与 C++ ``CodexSnapshot`` 一一对应。"""

    state: str = "unavailable"
    thread_id: str = ""
    turn_id: str = ""
    tool: str = ""
    item_id: str = ""
    started_at: int = 0
    detail: str = "未检测到可读取的本机 Codex 任务"

    def as_dict(self) -> dict:
        return asdict(self)


#: 任何 sqlite/OS/类型错误都折叠成这份「暂不可读」快照（与 C++ unreadableSnapshot 一致）。
_UNREADABLE = CodexSnapshot(detail="任务记录暂不可读或版本不兼容；稍后自动重试")

#: Python: re.fullmatch(r'[A-Za-z0-9-]{1,100}', candidate)
_RUN_ID_RE = re.compile(r"[A-Za-z0-9-]{1,100}")

#: kind → 工具名映射：照搬 C++ ``kindToTool``。
_KIND_TO_TOOL = {
    "commandExecution": "shell",
    "fileChange": "edit",
    "webSearch": "web_search",
    "imageGeneration": "image_generation",
    "imageView": "image_view",
    "mcpToolCall": "tool",
}

#: item 状态：需要用户确认/回答。
_APPROVAL_STATES = frozenset(
    {"waitingForApproval", "pendingApproval", "waiting_for_approval", "waitingForInput"}
)

#: item 状态：仍在执行。
_RUNNING_STATES = frozenset({"inProgress", "in_progress", "running"})


def _readonly_uri(path: Path) -> str:
    """构造只读 URI。``as_uri`` 会做百分号转义，中文用户名等路径也安全。"""
    return path.resolve().as_uri() + "?mode=ro"


def _history_id(public_id: str, rollout_path: str) -> str:
    """rollout 文件名末段若形如 run id，则作为 thread_history 里的键。

    对照 C++ ``historyId``：``<prefix>_<run_id>.jsonl`` 的 stem 取最后一个 ``_``
    之后的部分；整个 stem 命中 run id 形态且与 stem 不同才采用。
    """
    if not rollout_path:
        return public_id
    stem = Path(rollout_path).stem
    separator = stem.rfind("_")
    candidate = stem[separator + 1:] if separator >= 0 else stem
    if _RUN_ID_RE.fullmatch(candidate) and candidate != stem:
        return candidate
    return public_id


class CodexStatusReader:
    """有界读取本机 Codex 投影库；绝不创建、索引、迁移或写入。"""

    def __init__(self, home: str | Path = "") -> None:
        home = str(home).strip()
        if not home:
            home = os.environ.get("CODEX_HOME", "").strip() or str(Path.home() / ".codex")
        self._home = Path(home)

    def read(self, thread_id: str = "") -> CodexSnapshot:
        """读取当前任务快照。任何异常都退化为「暂不可读」快照，绝不外抛。"""
        try:
            return self._read_internal(thread_id)
        except Exception:
            return _UNREADABLE

    def database_path(self, prefix: str) -> str:
        """形如 ``<home>/<prefix>_<N>.sqlite``，取 N 最大者；找不到返回空串。"""
        if not self._home.is_dir():
            return ""
        best_path = ""
        best_suffix = -1
        for entry in self._home.glob(f"{prefix}_*.sqlite"):
            if not entry.is_file():
                continue
            suffix = entry.stem[len(prefix) + 1:]
            try:
                value = int(suffix)
            except ValueError:
                continue
            if value > best_suffix:
                best_suffix = value
                best_path = str(entry)
        return best_path

    def _open_readonly(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(_readonly_uri(Path(path)), timeout=0.04, uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    def _read_internal(self, thread_id: str) -> CodexSnapshot:
        history_path = self.database_path("thread_history")
        state_path = self.database_path("state")
        if not history_path or not state_path:
            return CodexSnapshot()

        # ---- 阶段一：从 state 库列出候选对话 ----
        rows: list[tuple[str, str]] = []  # (public_id, rollout_path)
        conn = self._open_readonly(state_path)
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()
            }
            where = "archived=0"
            if "agent_path" in columns:
                where += " AND (agent_path IS NULL OR agent_path='' OR agent_path='/root')"

            if thread_id:
                cursor = conn.execute(
                    f"SELECT id,rollout_path FROM threads WHERE {where} "
                    "AND (id=? OR rollout_path LIKE ?) LIMIT 2",
                    (thread_id, f"%_{thread_id}.jsonl"),
                )
            else:
                order = "updated_at DESC" if "updated_at" in columns else "rowid DESC"
                cursor = conn.execute(
                    f"SELECT id,rollout_path FROM threads WHERE {where} ORDER BY {order} LIMIT 64"
                )
            for row in cursor.fetchall():
                rows.append((str(row[0]), str(row[1] or "")))
        finally:
            conn.close()

        id_map: dict[str, str] = {}  # history_id -> public_id
        for public_id, rollout_path in rows:
            id_map[_history_id(public_id, rollout_path)] = public_id
        if not id_map:
            return CodexSnapshot(
                detail="尚无本机对话" if not thread_id else "固定对话不存在或已归档"
            )

        # ---- 阶段二：从 thread_history 库取最新回合 ----
        conn = self._open_readonly(history_path)
        try:
            placeholders = ",".join("?" for _ in id_map)
            row = conn.execute(
                f"SELECT thread_id,turn_id,status,started_at FROM thread_turns "
                f"WHERE thread_id IN ({placeholders}) "
                "ORDER BY started_at DESC,turn_id DESC LIMIT 1",
                tuple(id_map),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return CodexSnapshot(detail="尚无可监听的任务回合")

        session, turn, status, started_at = str(row[0]), str(row[1]), str(row[2]), row[3]
        started_at = int(started_at) if started_at is not None else 0
        public_thread_id = id_map.get(session, session)

        # ---- 阶段三：终态直接返回，否则读取最新 item 元数据 ----
        if status in ("completed", "interrupted", "failed", "error"):
            if status == "completed":
                return CodexSnapshot(
                    state="idle", thread_id=public_thread_id, turn_id=turn,
                    started_at=started_at, detail="任务完成",
                )
            if status == "interrupted":
                return CodexSnapshot(
                    state="interrupted", thread_id=public_thread_id, turn_id=turn,
                    started_at=started_at, detail="任务已中断",
                )
            return CodexSnapshot(
                state="error", thread_id=public_thread_id, turn_id=turn,
                started_at=started_at, detail="任务失败",
            )
        if status not in ("inProgress", "in_progress", "running"):
            return CodexSnapshot(
                thread_id=public_thread_id, turn_id=turn, started_at=started_at,
                detail=f"暂不支持的任务状态：{status[:40]}",
            )

        conn = self._open_readonly(history_path)
        try:
            # SQLite 只取出少量元数据，不把对话正文/命令参数/工具输出/推理内容读进内存。
            item = conn.execute(
                "SELECT item_id,"
                "json_extract(item_json,'$.type'),"
                "json_extract(item_json,'$.status'),"
                "json_extract(item_json,'$.tool'),"
                "json_array_length(item_json,'$.questions') "
                "FROM thread_items WHERE thread_id=? AND turn_id=? "
                "ORDER BY rollout_ordinal DESC LIMIT 1",
                (session, turn),
            ).fetchone()
        finally:
            conn.close()
        if item is None:
            return CodexSnapshot(
                state="thinking", thread_id=public_thread_id, turn_id=turn,
                started_at=started_at, detail="正在思考",
            )

        item_id = str(item[0] or "")
        kind = str(item[1] or "")
        item_status = str(item[2] or "")
        tool_name = item[3]
        questions = item[4]

        snapshot = CodexSnapshot(
            thread_id=public_thread_id, turn_id=turn, started_at=started_at,
            # Python: str(tool_name or tools.get(kind, ''))[:100]
            tool=(str(tool_name) if tool_name else _KIND_TO_TOOL.get(kind, ""))[:100],
            item_id=item_id,
        )

        # json_array_length 返回整数或 NULL；对照 C++ 的 hasQuestions 判定。
        has_questions = questions is not None and (
            (isinstance(questions, int) and questions > 0)
            or (not isinstance(questions, int) and str(questions) != "")
        )

        if item_status in _APPROVAL_STATES or has_questions:
            snapshot = CodexSnapshot(
                state="attention", thread_id=snapshot.thread_id, turn_id=snapshot.turn_id,
                tool=snapshot.tool, item_id=snapshot.item_id,
                started_at=started_at, detail="需要你确认或回答",
            )
        elif "request_user_input" in snapshot.tool and item_status in _RUNNING_STATES:
            snapshot = CodexSnapshot(
                state="attention", thread_id=snapshot.thread_id, turn_id=snapshot.turn_id,
                tool=snapshot.tool, item_id=snapshot.item_id,
                started_at=started_at, detail="正在等你回答",
            )
        elif kind in _KIND_TO_TOOL:
            snapshot = CodexSnapshot(
                state="working", thread_id=snapshot.thread_id, turn_id=snapshot.turn_id,
                tool=snapshot.tool, item_id=snapshot.item_id,
                started_at=started_at, detail="正在执行任务",
            )
        else:
            snapshot = CodexSnapshot(
                state="thinking", thread_id=snapshot.thread_id, turn_id=snapshot.turn_id,
                tool=snapshot.tool, item_id=snapshot.item_id,
                started_at=started_at, detail="正在思考或生成回复",
            )
        return snapshot