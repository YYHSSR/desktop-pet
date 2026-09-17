# -*- coding: utf-8 -*-
"""配置文件的持久化与写盘失败处理。"""
from __future__ import annotations

from pet.config import Config


def test_default_config_uses_plain_file(tmp_path):
    config = Config(base=tmp_path)
    assert config.path.name == "config.json"


def test_custom_settings_persist_through_reload(tmp_path):
    """设置项必须能从磁盘重载，重启后音乐开关/锁定位置等不丢失。"""
    config = Config(base=tmp_path)
    config.set("music_sing_enabled", True)
    config.set("lock_position", True)
    config.set("shift_drag", True)
    config.save()

    reloaded = Config(base=tmp_path)
    assert reloaded.get("music_sing_enabled") is True
    assert reloaded.get("lock_position") is True
    assert reloaded.get("shift_drag") is True


def test_save_returns_false_on_write_failure(tmp_path):
    """写盘失败（此处置目标为目录迫使 os.replace 失败）时 save 返回 False。"""
    config = Config(base=tmp_path)
    config.path.mkdir(parents=True, exist_ok=True)
    assert config.save() is False
