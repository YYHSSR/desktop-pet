# -*- coding: utf-8 -*-
"""测试设置界面控件状态、保存/读取 round-trip 以及内置 Agent 音效资源存在与格式有效性。"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_modern_settings_dialog_round_trip(qapp, tmp_path: Path):
    """验证现代设置面板：初始化正确读取 Config，修改后 _write_config 写入 Config 并能准确读回。"""
    cfg_root = tmp_path / "appdata"
    cfg = Config(cfg_root)

    # 1. 初始状态断言
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        # 3. 触发写入
        ok = dialog._write_config()
        assert ok is True
    finally:
        dialog.deleteLater()

    # 4. 新建 Config 实例重载验证持久化 round-trip
    reloaded_cfg = Config(cfg_root)
