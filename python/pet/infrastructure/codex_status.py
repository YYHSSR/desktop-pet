"""Read-only adapter for local Codex history projections.

This is a version-tolerant local adapter, not a public OpenAI API. Unsupported
schemas return unavailable. Only lifecycle and tool metadata leave this module.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import time
import re


@dataclass(frozen=True)
class CodexSnapshot:
    state: str = 'unavailable'
    thread_id: str = ''
    turn_id: str = ''
    tool: str = ''
    item_id: str = ''
    started_at: int = 0
    detail: str = '未检测到可读取的本机 Codex 任务'


class CodexStatusReader:
    """Bounded reads; never creates, indexes, migrates or writes Codex databases."""

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')

    def _database(self, prefix: str) -> Path | None:
        candidates = []
        for path in self.home.glob(f'{prefix}_*.sqlite'):
            suffix = path.stem.removeprefix(prefix + '_')
            if suffix.isdigit() and path.is_file():
                candidates.append((int(suffix), path))
        return max(candidates)[1] if candidates else None

    @staticmethod
    def _connect(path: Path):
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.04)
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 0.06
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return db

    @staticmethod
    def _history_id(public_id: str, rollout_path: object) -> str:
        """Map a sidebar task to the per-run id used by thread_history.

        Current Codex desktop rollouts end in ``_<run-id>.jsonl`` while older
        versions use the sidebar id directly. Keep both layouts readable.
        """
        try:
            stem = Path(str(rollout_path or '')).stem
            candidate = stem.rsplit('_', 1)[-1]
        except (TypeError, ValueError):
            return public_id
        if re.fullmatch(r'[A-Za-z0-9-]{1,100}', candidate) and candidate != stem:
            return candidate
        return public_id

    def read(self, thread_id: str = '') -> CodexSnapshot:
        try:
            return self._read(thread_id)
        except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
            return CodexSnapshot(detail='任务记录暂不可读或版本不兼容；稍后自动重试')

    def _read(self, thread_id: str) -> CodexSnapshot:
        history = self._database('thread_history')
        state = self._database('state')
        if history is None or state is None:
            return CodexSnapshot()
        with closing(self._connect(state)) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
            where = 'archived=0'
            if 'agent_path' in columns:
                where += " AND (agent_path IS NULL OR agent_path='' OR agent_path='/root')"
            if thread_id:
                rows = db.execute(
                    f'SELECT id,rollout_path FROM threads WHERE {where} '
                    'AND (id=? OR rollout_path LIKE ?) LIMIT 2',
                    (thread_id, f'%_{thread_id}.jsonl'),
                ).fetchall()
            else:
                order = 'updated_at DESC' if 'updated_at' in columns else 'rowid DESC'
                rows = db.execute(f'SELECT id,rollout_path FROM threads WHERE {where} ORDER BY {order} LIMIT 64').fetchall()
        id_map = {self._history_id(str(public_id), rollout): str(public_id) for public_id, rollout in rows}
        if not id_map:
            return CodexSnapshot(detail='固定对话不存在或已归档' if thread_id else '尚无本机对话')
        with closing(self._connect(history)) as db:
            history_ids = list(id_map)
            placeholders = ','.join('?' for _ in history_ids)
            row = db.execute(
                f'SELECT thread_id,turn_id,status,started_at FROM thread_turns '
                f'WHERE thread_id IN ({placeholders}) ORDER BY started_at DESC,turn_id DESC LIMIT 1', history_ids,
            ).fetchone()
            if row is None:
                return CodexSnapshot(detail='尚无可监听的任务回合')
            session, turn, status, started = row
            common = dict(thread_id=id_map.get(str(session), str(session)), turn_id=turn, started_at=int(started or 0))
            if status in {'completed', 'interrupted', 'failed', 'error'}:
                outcome = {'completed':'idle', 'interrupted':'interrupted', 'failed':'error', 'error':'error'}[status]
                return CodexSnapshot(state=outcome, detail={'idle':'任务完成','interrupted':'任务已中断','error':'任务失败'}[outcome], **common)
            if status not in {'inProgress', 'in_progress', 'running'}:
                return CodexSnapshot(detail=f'暂不支持的任务状态：{str(status)[:40]}', **common)
            # SQLite extracts only small metadata values, not conversation text,
            # command arguments, tool output or reasoning content into Python.
            item = db.execute(
                "SELECT item_id,json_extract(item_json,'$.type'),json_extract(item_json,'$.status'),"
                "json_extract(item_json,'$.tool'),json_array_length(item_json,'$.questions') "
                "FROM thread_items WHERE thread_id=? AND turn_id=? ORDER BY rollout_ordinal DESC LIMIT 1",
                (session, turn),
            ).fetchone()
        if item is None:
            return CodexSnapshot(state='thinking', detail='正在思考', **common)
        item_id, kind, item_status, tool_name, questions = item
        tools = {'commandExecution':'shell','fileChange':'edit','webSearch':'web_search',
                 'imageGeneration':'image_generation','imageView':'image_view','mcpToolCall':'tool'}
        tool = str(tool_name or tools.get(kind, ''))[:100]
        if item_status in {'waitingForApproval','pendingApproval','waiting_for_approval','waitingForInput'} or questions:
            current, detail = 'attention', '需要你确认或回答'
        elif 'request_user_input' in tool and item_status in {'inProgress','in_progress','running'}:
            current, detail = 'attention', '正在等你回答'
        elif kind in tools:
            current, detail = 'working', '正在执行任务'
        else:
            current, detail = 'thinking', '正在思考或生成回复'
        return CodexSnapshot(state=current, tool=tool, item_id=str(item_id), detail=detail, **common)
