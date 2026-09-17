# -*- coding: utf-8 -*-
"""Unit tests for web links submenu, Antigravity IDE hooks/status linkage, and desktop-pet renaming."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from pet.config import (
    APP_NAME,
    APP_DIR_NAME,
    DEFAULT_QUICK_WEBSITES,
    Config,
    _clean_quick_websites,
)
from pet.context_menus.quick_launch import (
    add_web_links_menu,
    configured_quick_websites,
    open_quick_website,
)
from pet.modern_settings_dialog import WebsiteLinksEditor, WebsiteEditDialog
from pet.agent_link import (
    AntigravityMonitor,
    antigravity_event_state,
    antigravity_event_tool,
    normalize_event_state,
)
from pet.window import STREAM_CAPTURE_TITLE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ======================================================================
# 1. 软件重命名测试
# ======================================================================

def test_desktop_pet_app_name_and_constants():
    assert APP_NAME == "desktop-pet"
    assert APP_DIR_NAME == "desktop-pet"
    assert STREAM_CAPTURE_TITLE == "desktop-pet 桌宠"


def test_quick_websites_defaults():
    assert len(DEFAULT_QUICK_WEBSITES) >= 1
    assert DEFAULT_QUICK_WEBSITES[0]["name"] == "GitHub 项目页"


def test_clean_quick_websites():
    # 缺失协议时自动补全 https://
    cleaned = _clean_quick_websites([{"name": "测试", "url": "example.com"}])
    assert cleaned[0]["url"] == "https://example.com"
    assert cleaned[0]["name"] == "测试"

    # 空列表保留空列表（允许用户清空网址），None 或非列表回退默认
    assert _clean_quick_websites([]) == []
    assert _clean_quick_websites(None) == DEFAULT_QUICK_WEBSITES
    assert _clean_quick_websites("invalid") == DEFAULT_QUICK_WEBSITES

    # 未命名保留空名称
    cleaned2 = _clean_quick_websites([{"name": "", "url": "https://google.com"}])
    assert cleaned2[0]["name"] == ""
    assert cleaned2[0]["url"] == "https://google.com"


def test_quick_websites_persistence(tmp_path):
    cfg = Config(tmp_path)
    custom = [
        {"name": "我的主页", "url": "https://myhome.example"},
        {"name": "", "url": "https://noname.example"},
    ]
    cfg.set("quick_websites", custom)
    cfg.save()

    cfg2 = Config(tmp_path)
    loaded = cfg2.get("quick_websites")
    assert len(loaded) == 2
    assert loaded[0]["name"] == "我的主页"
    assert loaded[0]["url"] == "https://myhome.example"
    assert loaded[1]["name"] == ""
    assert loaded[1]["url"] == "https://noname.example"


def test_add_web_links_menu(qapp, monkeypatch):
    class FakePet:
        def __init__(self):
            self.cfg = {
                "quick_websites": [
                    {"name": "命名网页", "url": "https://named.example"},
                    {"name": "", "url": "https://unnamed.example"},
                ]
            }

    opened_urls = []
    monkeypatch.setattr(
        "pet.context_menus.quick_launch.QDesktopServices.openUrl",
        lambda url: opened_urls.append(url.toString()) or True,
    )

    menu = QMenu()
    sub = add_web_links_menu(menu, FakePet())
    assert sub is not None
    assert sub.title() == "快捷网址"

    actions = sub.actions()
    assert len(actions) == 2
    # 命名的显示名称，未命名的显示网址
    assert actions[0].text() == "命名网页"
    assert actions[1].text() == "https://unnamed.example"

    # 触发点击打开
    actions[0].trigger()
    assert opened_urls == ["https://named.example"]
    actions[1].trigger()
    assert opened_urls == ["https://named.example", "https://unnamed.example"]

    # 测试清空网址后显示添加提示
    class FakePetEmpty:
        def __init__(self):
            self.cfg = {"quick_websites": []}

    menu2 = QMenu()
    sub2 = add_web_links_menu(menu2, FakePetEmpty())
    assert sub2.title() == "快捷网址"
    assert len(sub2.actions()) == 1
    assert sub2.actions()[0].text() == "去设置中添加网址..."


def test_website_links_editor_widget(qapp):
    editor = WebsiteLinksEditor(DEFAULT_QUICK_WEBSITES)
    assert len(editor.websites()) == len(DEFAULT_QUICK_WEBSITES)

    # 添加自定义网站
    editor.add_website({"name": "测试站点", "url": "https://test.com"})
    sites = editor.websites()
    assert len(sites) == len(DEFAULT_QUICK_WEBSITES) + 1
    assert sites[-1]["name"] == "测试站点"
    assert sites[-1]["url"] == "https://test.com"

    # 勾选并移除
    item = editor.list.item(len(sites) - 1)
    item.setCheckState(Qt.CheckState.Checked)
    editor._remove_checked()
    assert len(editor.websites()) == len(DEFAULT_QUICK_WEBSITES)

    # 重置默认
    editor._reset_defaults()
    assert len(editor.websites()) == len(DEFAULT_QUICK_WEBSITES)


# ======================================================================
# 3. Antigravity IDE 联动与 Hook 测试
# ======================================================================

def test_antigravity_event_state_lifecycle():
    # 测试新增生命周期事件解析
    assert antigravity_event_state({"event": "PreInvocation"}) == "thinking"
    assert antigravity_event_state({"event": "PreToolUse"}) == "working"
    assert antigravity_event_state({"event": "PostToolUse"}) == "working"
    assert antigravity_event_state({"event": "Stop"}) == "idle"
    assert antigravity_event_state({"state": "thinking"}) == "thinking"
    assert antigravity_event_state({"state": "working"}) == "working"
    assert antigravity_event_state({"state": "idle"}) == "idle"

    assert normalize_event_state("PreInvocation") == "thinking"
    assert normalize_event_state("PostInvocation") == "working"


def test_antigravity_hooks_installation_and_cleanup(tmp_path):
    events_file = tmp_path / "agent-events" / "antigravity.jsonl"
    hooks_file = tmp_path / "hooks.json"

    # 初始安装
    ok = AntigravityMonitor.install_hooks(events_file, hooks_path=hooks_file)
    assert ok is True
    assert hooks_file.is_file()

    data = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert AntigravityMonitor.HOOK_KEY in data
    hook_entry = data[AntigravityMonitor.HOOK_KEY]
    assert "PreInvocation" in hook_entry
    assert "PreToolUse" in hook_entry
    assert "PostToolUse" in hook_entry
    assert "Stop" in hook_entry
    assert hook_entry.get(AntigravityMonitor.HOOK_FLAG) is True

    # 卸载清理
    un_ok = AntigravityMonitor.uninstall_hooks(hooks_path=hooks_file)
    assert un_ok is True
    data_after = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert AntigravityMonitor.HOOK_KEY not in data_after


def test_antigravity_python_hook_script_execution(tmp_path):
    events_file = tmp_path / "agent-events" / "antigravity.jsonl"
    script, _ = AntigravityMonitor._ensure_hook_script(events_file)
    assert script.is_file()

    # 1. 模拟 PreInvocation (thinking)
    proc1 = subprocess.run(
        [sys.executable, str(script), "PreInvocation"],
        input="",
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc1.stdout.strip()) == {}

    # 2. 模拟 PreToolUse (working, run_command)
    tool_input = json.dumps({
        "toolCall": {"name": "run_command", "args": {"CommandLine": "dir"}}
    })
    proc2 = subprocess.run(
        [sys.executable, str(script), "PreToolUse"],
        input=tool_input,
        capture_output=True,
        text=True,
        check=True,
    )
    res2 = json.loads(proc2.stdout.strip())
    assert res2 == {"decision": "allow"}

    # 3. 模拟 Stop (idle)
    proc3 = subprocess.run(
        [sys.executable, str(script), "Stop"],
        input="",
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc3.stdout.strip()) == {}

    # 检查写入 jsonl 的事件记录
    lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3
    assert lines[0]["state"] == "thinking"
    assert lines[1]["state"] == "working"
    assert lines[1]["tool"] == "run_command"
    assert lines[2]["state"] == "idle"


def test_antigravity_extract_tool_from_metadata():
    # 测试已知工具匹配
    meta = b'\n\x0c\x08\xd8\xfe\xea\xd4\x06\x10\xdc\xfc\xa7\xe3\x02\x18\x02"\xc1 \n\ncall_32595\x12\x0brun_command\x1a\xc6\x04'
    assert AntigravityMonitor._extract_tool_from_metadata(meta) == "run_command"

    meta_view = b'\n\x0c\x08"\xea^\n\x0ccall_3258469\x12\tview_file\x1a\xea\x01'
    assert AntigravityMonitor._extract_tool_from_metadata(meta_view) == "view_file"

    assert AntigravityMonitor._extract_tool_from_metadata(b'') == ""
    assert AntigravityMonitor._extract_tool_from_metadata(None) == ""


def test_antigravity_sqlite_db_polling(tmp_path):
    import sqlite3

    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True)
    db_file = conv_dir / "test_conv.db"

    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE steps (idx INTEGER, step_type INTEGER, status INTEGER, metadata BLOB)")
    # 写入一个正在运行的 run_command 步骤 (status = 2)
    meta = b'\x12\x0brun_command'
    con.execute("INSERT INTO steps VALUES (1, 21, 2, ?)", (meta,))
    con.commit()
    con.close()

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()

    mon = AntigravityMonitor(cfg_dir, base_dir=brain_dir)
    # mock conversations 查找路径
    states, activities = [], []
    mon.state_changed.connect(lambda k, s: states.append(s))
    mon.activity.connect(lambda k, a: activities.append(a))

    mon._poll_active_db()
    assert states == ["working"]
    assert activities == ["run_command"]

    # 步骤完成 (status = 3)
    con = sqlite3.connect(db_file)
    con.execute("UPDATE steps SET status = 3 WHERE idx = 1")
    con.commit()
    con.close()

    mon._poll_active_db()
    assert states == ["working", "idle"]


def test_antigravity_sqlite_db_polling_thinking_and_wal(tmp_path):
    import sqlite3
    import time
    import os

    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir(parents=True)
    db_file = conv_dir / "test_wal_conv.db"
    wal_file = conv_dir / "test_wal_conv.db-wal"

    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE steps (idx INTEGER, step_type INTEGER, status INTEGER, metadata BLOB)")
    # step_type 15 = PLANNER_RESPONSE (thinking), status 2 = RUNNING
    con.execute("INSERT INTO steps VALUES (1, 15, 2, NULL)")
    con.commit()
    con.close()

    # Set db mtime to 1000s ago (older than 600s threshold)
    past = time.time() - 1000
    os.utime(db_file, (past, past))
    # Write WAL file with current time
    wal_file.write_bytes(b"wal data")

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()

    mon = AntigravityMonitor(cfg_dir, base_dir=brain_dir)
    states = []
    mon.state_changed.connect(lambda k, s: states.append(s))

    mon._poll_active_db()
    assert states == ["thinking"]

    # 模拟步骤结束 (status = 3)
    con = sqlite3.connect(db_file)
    con.execute("UPDATE steps SET status = 3 WHERE idx = 1")
    con.commit()
    con.close()
    wal_file.write_bytes(b"wal updated")

    mon._poll_active_db()
    assert states == ["thinking", "idle"]


# ======================================================================
# 4. 启动应用与多开槽位快捷项同步与持久化测试
# ======================================================================

def test_add_app_launch_menu(qapp):
    from pet.context_menus.quick_launch import add_app_launch_menu, add_agent_launch_menu

    class FakePet:
        def __init__(self):
            self.cfg = {
                "quick_launch_apps": [
                    {"name": "我的工具", "path": "tool.exe", "kind": "application"},
                ]
            }

    menu = QMenu()
    sub = add_app_launch_menu(menu, FakePet())
    assert sub is not None
    assert sub.title() == "启动应用"
    assert len(sub.actions()) == 1
    assert sub.actions()[0].text() == "我的工具"

    # 向后兼容别名
    menu2 = QMenu()
    sub2 = add_agent_launch_menu(menu2, FakePet())
    assert sub2.title() == "启动应用"

    # 空列表显示提示
    class FakePetEmpty:
        def __init__(self):
            self.cfg = {"quick_launch_apps": []}

    menu3 = QMenu()
    sub3 = add_app_launch_menu(menu3, FakePetEmpty())
    assert sub3.title() == "启动应用"
    assert len(sub3.actions()) == 1
    assert sub3.actions()[0].text() == "去设置中添加应用..."


def test_quick_launch_apps_stem_fallback():
    from pet.config import _clean_quick_launch_apps
    # 当未指定名称时，自动回退到 Path(path).stem
    cleaned = _clean_quick_launch_apps([{"name": "", "path": "C:/apps/my_tool.exe"}])
    assert len(cleaned) == 1
    assert cleaned[0]["name"] == "my_tool"
    assert cleaned[0]["path"] == "C:/apps/my_tool.exe"


def test_quick_launch_editor_widget(qapp):
    from pet.modern_settings_dialog import QuickLaunchEditor, AppEditDialog

    apps = [
        {"name": "测试应用", "path": "C:/test.exe", "kind": "application"},
    ]
    editor = QuickLaunchEditor(apps)
    assert len(editor.apps()) == 1
    assert hasattr(editor, "edit_button")
    assert editor.apps()[0]["name"] == "测试应用"

