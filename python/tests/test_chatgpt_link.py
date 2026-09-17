import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def codex_home(tmp_path):
    home = tmp_path / 'codex'
    home.mkdir()
    with sqlite3.connect(home / 'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER, agent_path TEXT)')
        db.executemany('INSERT INTO threads VALUES (?,?,0,?)', [('one', '', '/root'), ('two', '', '/root'), ('child', '', '/root/sub')])
    with sqlite3.connect(home / 'thread_history_1.sqlite') as db:
        db.execute('CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, started_at INTEGER, completed_at INTEGER, PRIMARY KEY(thread_id,turn_id))')
        db.execute('CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, item_json TEXT, PRIMARY KEY(thread_id,turn_id,item_id))')
        db.executemany('INSERT INTO thread_turns VALUES (?,?,?,?,?)', [('one','a','inProgress',100,None), ('two','b','completed',90,95), ('child','c','inProgress',110,None)])
        db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)', ('one','a','x',1,json.dumps({'type':'reasoning','content':'private'})))
    return home


def set_turn(home, status):
    with sqlite3.connect(home / 'thread_history_1.sqlite') as db:
        db.execute('UPDATE thread_turns SET status=? WHERE thread_id=?', (status,'one'))


def test_reads_real_turn_without_conversation_content(codex_home):
    from pet.infrastructure.codex_status import CodexStatusReader
    snapshot = CodexStatusReader(codex_home).read()
    assert (snapshot.thread_id, snapshot.turn_id, snapshot.state) == ('one','a','thinking')
    assert 'private' not in repr(snapshot)


@pytest.mark.parametrize('status,state', [('completed','idle'),('interrupted','interrupted'),('failed','error'),('unknown-future','unavailable')])
def test_turn_outcomes_are_distinct(codex_home, status, state):
    from pet.infrastructure.codex_status import CodexStatusReader
    set_turn(codex_home, status)
    assert CodexStatusReader(codex_home).read().state == state


def test_can_pin_a_conversation(codex_home):
    from pet.infrastructure.codex_status import CodexStatusReader
    snapshot = CodexStatusReader(codex_home).read(thread_id='two')
    assert snapshot.thread_id == 'two'
    assert snapshot.state == 'idle'


def test_new_codex_window_maps_rollout_runtime_id_to_sidebar_thread(tmp_path):
    """New desktop tasks store a UI thread id and a different history projection id."""
    from pet.infrastructure.codex_status import CodexStatusReader

    home = tmp_path / 'codex'
    home.mkdir()
    public_id = '01a06d13-c50c-7b91-a0c0-d8b54d407bdf'
    runtime_id = '01a06dcc-c41a-74f2-b2a8-c59e49925df5'
    rollout = f'C:/Codex/sessions/rollout-2026-09-05-{public_id}_{runtime_id}.jsonl'
    with sqlite3.connect(home / 'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER, agent_path TEXT, updated_at INTEGER)')
        db.execute('INSERT INTO threads VALUES (?,?,0,NULL,?)', (public_id, rollout, 200))
    with sqlite3.connect(home / 'thread_history_1.sqlite') as db:
        db.execute('CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, started_at INTEGER)')
        db.execute('CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, item_json TEXT)')
        db.execute('INSERT INTO thread_turns VALUES (?,?,?,?)', (runtime_id, 'live-turn', 'inProgress', 300))
        db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?)', (runtime_id, 'live-turn', 'item', 1, json.dumps({'type':'commandExecution','status':'inProgress'})))

    automatic = CodexStatusReader(home).read()
    assert (automatic.thread_id, automatic.turn_id, automatic.state) == (public_id, 'live-turn', 'working')


def test_missing_pin_does_not_follow_another_task(codex_home):
    from pet.infrastructure.codex_status import CodexStatusReader
    assert CodexStatusReader(codex_home).read(thread_id='missing').state == 'unavailable'


def test_legacy_pinned_thread_setting_is_removed(tmp_path):
    from pet.config import Config
    cfg = Config(tmp_path)
    settings = dict(cfg.get('agent_link'))
    settings['chatgpt_thread_id'] = 'old-pinned-task'
    cfg.set('agent_link', settings)
    assert 'chatgpt_thread_id' not in cfg.get('agent_link')


def test_unknown_schema_degrades_without_creating_files(tmp_path):
    from pet.infrastructure.codex_status import CodexStatusReader
    reader = CodexStatusReader(tmp_path)
    assert reader.read().state == 'unavailable'
    assert list(tmp_path.iterdir()) == []


def test_reader_never_changes_database(codex_home):
    from pet.infrastructure.codex_status import CodexStatusReader
    before = {p.name:p.read_bytes() for p in codex_home.iterdir()}
    CodexStatusReader(codex_home).read()
    assert before == {p.name:p.read_bytes() for p in codex_home.iterdir()}


def test_tool_activity_and_approval(codex_home):
    from pet.infrastructure.codex_status import CodexStatusReader
    with sqlite3.connect(codex_home / 'thread_history_1.sqlite') as db:
        db.execute('UPDATE thread_items SET item_json=?', (json.dumps({'type':'commandExecution','status':'waitingForApproval','command':'secret'}),))
    snapshot = CodexStatusReader(codex_home).read()
    assert (snapshot.state, snapshot.tool) == ('attention', 'shell')
    assert 'secret' not in repr(snapshot)


def test_menu_chatgpt_immediately_follows_antigravity(tmp_path):
    from PySide6.QtWidgets import QApplication, QMenu
    from pet.config import Config
    from pet.context_menus.shared import add_agent_link_menu
    app = QApplication.instance() or QApplication([])
    class Pet:
        cfg = Config(tmp_path)
        _toggle_agent_link = lambda *args: None
        _set_agent_link_option = lambda *args: None
    menu = QMenu()
    add_agent_link_menu(menu, Pet())
    labels = [a.text() for a in menu.actions()[0].menu().actions()]
    assert labels[:2] == ['Antigravity IDE', 'ChatGPT']
    pass


def test_disabled_manager_does_not_write_hooks(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.agent_link import AgentLinkManager, AntigravityMonitor
    app = QApplication.instance() or QApplication([])
    writes = []
    monkeypatch.setattr(AntigravityMonitor, 'install_hooks', lambda *args: writes.append(args))
    manager = AgentLinkManager(object(), Config(tmp_path))
    assert writes == []
    assert not any(m.is_running() for m in manager.monitors.values())


def test_settings_close_has_no_removed_chat_dependency(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pet.app import PetApp
    import pet.app as module
    owner = PetApp.__new__(PetApp)
    owner.config = SimpleNamespace(get=lambda *args: True)
    owner.win = None
    owner._update_bubble_suppression_for_settings = lambda: None
    monkeypatch.setattr(module, '_mac_set_dock_icon_visible', lambda value: None)
    owner._modern_settings_finished(0)


def test_completion_timer_is_bounded_and_reused(tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.agent_link import AgentLinkManager
    app = QApplication.instance() or QApplication([])
    manager = AgentLinkManager(object(), Config(tmp_path))
    manager._schedule_done_check('custom')
    timer = manager._done_pending['custom']
    count = len(manager.findChildren(QTimer))
    manager._fire_done('custom')
    assert not timer.isActive()
    assert 'custom' not in manager._done_pending
    for _ in range(100):
        manager._schedule_done_check('custom')
        assert manager._done_pending['custom'] is timer
        manager._fire_done('custom')
    assert len(manager.findChildren(QTimer)) == count


def test_monitor_baseline_and_conversation_switch_do_not_replay_completion(tmp_path):
    from pet.services.chatgpt_monitor import ChatGPTMonitor
    from pet.infrastructure.codex_status import CodexSnapshot
    monitor = ChatGPTMonitor(tmp_path)
    events = []
    monitor.state_changed.connect(lambda agent, state: events.append(state))
    monitor._publish(CodexSnapshot(state='idle',thread_id='one',turn_id='a'))
    assert events == []
    monitor._publish(CodexSnapshot(state='thinking',thread_id='one',turn_id='b'))
    monitor._publish(CodexSnapshot(state='idle',thread_id='one',turn_id='b'))
    assert events == ['thinking','idle']
    monitor._publish(CodexSnapshot(state='idle',thread_id='two',turn_id='c'))
    assert events == ['thinking','idle']


def test_real_database_monitor_runs_off_gui_and_stops(codex_home):
    import threading
    import time
    from PySide6.QtWidgets import QApplication
    from pet.services.chatgpt_monitor import ChatGPTMonitor
    from pet.infrastructure.codex_status import CodexStatusReader
    class Reader(CodexStatusReader):
        threads = []
        def read(self, thread_id=''):
            self.threads.append(threading.get_ident())
            return super().read(thread_id)
    reader = Reader(codex_home)
    monitor = ChatGPTMonitor(codex_home, reader=reader)
    events = []
    monitor.state_changed.connect(lambda agent, state: events.append(state))
    monitor.start()
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert events == ['thinking']
    assert all(t != threading.get_ident() for t in reader.threads)
    monitor.pause()
    count = len(events)
    set_turn(codex_home, 'completed')
    monitor._poll()
    assert len(events) == count
    monitor.resume()
    deadline = time.monotonic() + 3
    while 'idle' not in events and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    monitor.stop()
    assert 'idle' in events
    assert monitor._executor is None
    assert not monitor._timer.isActive()


def test_permission_wait_and_interruption_never_celebrate(tmp_path):
    from pet.agent_link import AgentLinkManager
    from pet.config import Config
    class Pet:
        cats = {'acts':['写代码','轻快记录','东张西望']}
        isVisible = lambda self: True
        request_link_anim = lambda *args: None
        request_link_idle = lambda *args: None
        show_bubble = lambda *args, **kwargs: None
    manager = AgentLinkManager(Pet(), Config(tmp_path))
    bubbles = []
    manager._show_link_bubble = lambda text, **kw: bubbles.append(text)
    manager._on_agent_state('chatgpt','working')
    manager._on_agent_state('chatgpt','attention')
    assert 'chatgpt' not in manager._done_pending
    assert '确认或回答' in bubbles[-1]
    manager._on_agent_state('chatgpt','interrupted')
    assert 'chatgpt' not in manager._done_pending
    manager._on_agent_state('chatgpt','thinking')
    manager._on_agent_state('chatgpt','idle')
    manager._fire_done('chatgpt')


def test_switching_task_clears_old_busy_state(tmp_path):
    from pet.agent_link import AgentLinkManager
    from pet.config import Config
    manager = AgentLinkManager(object(), Config(tmp_path))
    manager._last_raw['chatgpt'] = 'working'
    manager._schedule_done_check('chatgpt')
    manager._reset_chatgpt_task()
    assert 'chatgpt' not in manager._last_raw
    assert 'chatgpt' not in manager._done_pending


def test_disabling_chatgpt_clears_busy_animation(tmp_path):
    from pet.agent_link import AgentLinkManager
    from pet.config import Config
    manager = AgentLinkManager(object(),Config(tmp_path))
    manager._last_raw['chatgpt'] = 'working'
    assert manager.set_enabled('chatgpt',False)
    assert 'chatgpt' not in manager._last_raw
