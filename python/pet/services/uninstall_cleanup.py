# -*- coding: utf-8 -*-
"""卸载清理：删除自启项。

供 `--uninstall-cleanup` 参数使用——安装包（Inno Setup）卸载时调用。
该路径不启动 QApplication/事件循环，只做必要的清理，便于「无 Qt 依赖路径」
（仍可 import Qt 模块，但不建窗口）下执行。
"""

from __future__ import annotations

def run_uninstall_cleanup(config=None) -> dict:
    """执行卸载清理各步骤，返回结果字典（供测试与日志）。

    步骤：
    1. 删除当前变体开机自启项（autostart.disable）；
    """
    from pet.infrastructure import autostart

    if config is None:
        from pet.infrastructure.config import Config
        config = Config()

    return {"autostart": bool(autostart.disable())}
