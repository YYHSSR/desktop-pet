"""GUI-thread facade; local Codex reads run in one lazily created worker."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, QTimer, Signal

from pet.infrastructure.codex_status import CodexSnapshot, CodexStatusReader


class ChatGPTMonitor(QObject):
    state_changed = Signal(str, str)
    activity = Signal(str, str)
    snapshot_changed = Signal(object)
    task_changed = Signal()

    def __init__(self, config_dir, parent=None, *, reader=None):
        super().__init__(parent)
        self.agent_key = 'chatgpt'
        self._reader = reader or CodexStatusReader()
        self._running = False
        self._paused = False
        self._executor = None
        self._future = None
        self._generation = 0
        self._request_generation = 0
        self.snapshot = CodexSnapshot()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        # No callback from the worker touches this QObject after deletion.
        self._worker_holder = {}
        self.destroyed.connect(lambda *_, holder=self._worker_holder: _shutdown_worker(holder))

    def _dispose_worker(self, *_):
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
            self._worker_holder.clear()

    def is_running(self):
        return self._running and not self._paused

    def start(self):
        if self._running:
            return
        self._running = True
        self._paused = False
        self._generation += 1
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pet-codex-status')
        self._worker_holder['executor'] = self._executor
        self._timer.start(250)
        self._poll()

    def stop(self):
        self._running = False
        self._paused = False
        self._generation += 1
        self._timer.stop()
        self._dispose_worker()
        self._future = None
        self.snapshot = CodexSnapshot()

    def pause(self):
        if self._running:
            self._paused = True
            self._generation += 1
            self._timer.stop()

    def resume(self):
        if self._running and self._paused:
            self._paused = False
            self._generation += 1
            self._timer.start(250)

    def _poll(self):
        if not self.is_running():
            return
        if self._future is not None:
            if not self._future.done():
                return
            try:
                snapshot = self._future.result()
            except Exception:
                snapshot = CodexSnapshot(detail='监听暂不可用，稍后自动重试')
            self._future = None
            if self._request_generation == self._generation:
                self._publish(snapshot)
        if self._executor is not None:
            self._request_generation = self._generation
            self._future = self._executor.submit(self._reader.read)

    def _publish(self, snapshot):
        previous = self.snapshot
        changed_task = (previous.thread_id, previous.turn_id) != (snapshot.thread_id, snapshot.turn_id)
        self.snapshot = snapshot
        self._timer.setInterval(250 if snapshot.state in {'thinking','working','attention'} else 1000)
        if snapshot == previous:
            return
        if changed_task:
            self.task_changed.emit()
        self.snapshot_changed.emit(snapshot)
        # Baseline completed tasks and switching conversations must not celebrate
        # old history. Only a real transition on a tracked turn is a completion.
        if not changed_task or snapshot.state in {'thinking','working','attention','unavailable'}:
            self.state_changed.emit('chatgpt', snapshot.state)
        if snapshot.tool and (snapshot.item_id, snapshot.tool) != (previous.item_id, previous.tool):
            self.activity.emit('chatgpt', snapshot.tool)


def _shutdown_worker(holder):
    executor = holder.pop('executor', None)
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
