# -*- coding: utf-8 -*-
"""卸载清理（--uninstall-cleanup）各步骤的可 mock 单元测试。

覆盖：
- run_uninstall_cleanup 执行自启删除；
- __main__ 对 --uninstall-cleanup 的派发。
"""

from __future__ import annotations

import sys

from pet.config import Config
from pet import uninstall_cleanup


def _config(tmp_path) -> Config:
    return Config(base=tmp_path)


def test_uninstall_cleanup_runs_all_steps(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: True)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is True


def test_uninstall_cleanup_reports_step_failures(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("pet.autostart.disable", lambda: False)

    results = uninstall_cleanup.run_uninstall_cleanup(config)
    assert results["autostart"] is False


def test_main_dispatches_uninstall_cleanup(monkeypatch):
    import pet.__main__ as m
    monkeypatch.setattr(sys, "argv", ["pet", "--uninstall-cleanup"])
    monkeypatch.setattr(
        "pet.uninstall_cleanup.run_uninstall_cleanup", lambda: {"autostart": True}
    )
    assert m._main() == 0


def test_main_returns_nonzero_on_uninstall_failure(monkeypatch):
    import pet.__main__ as m
    monkeypatch.setattr(sys, "argv", ["pet", "--uninstall-cleanup"])
    monkeypatch.setattr(
        "pet.uninstall_cleanup.run_uninstall_cleanup", lambda: {"autostart": False}
    )
    assert m._main() != 0
