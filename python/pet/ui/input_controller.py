"""Per-pixel input adapter; its timer lives on the supplied widget's thread."""
from __future__ import annotations
import logging
from typing import Any, Protocol
from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtGui import QCursor
from pet.infrastructure.native_window import _set_windows_click_through


class PixelInputWindow(Protocol):
    def isVisible(self) -> bool: ...
    def mapFromGlobal(self, point: QPoint) -> QPoint: ...
    def width(self) -> int: ...
    def height(self) -> int: ...
    def winId(self) -> Any: ...
    def _is_transparent_at(self, point: QPoint) -> bool: ...

class WindowsPerPixelInputController:
    """根据光标所在像素动态切换 layered window 的输入穿透。

    HTTRANSPARENT 只能继续命中当前线程的窗口，无法穿透到其他应用。
    WS_EX_TRANSPARENT 会让 Windows 在命中时跳过 layered 桌宠窗口；独立
    定时器在窗口不再收到鼠标消息时仍能检测光标并恢复角色区域交互。
    """

    def __init__(self, window: PixelInputWindow) -> None:
        self._window = window
        self._timer = QTimer(window)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def should_click_through(self, global_pos: QPoint) -> bool:
        win = self._window
        if getattr(win, '_press_global', None) is not None or not win.isVisible():
            return False
        local = win.mapFromGlobal(global_pos)
        if not QRect(0, 0, win.width(), win.height()).contains(local):
            return False
        return win._is_transparent_at(local)

    def refresh(self) -> None:
        try:
            enabled = self.should_click_through(QCursor.pos())
            _set_windows_click_through(int(self._window.winId()), enabled)
        except Exception:
            logging.debug("更新 Windows 逐像素鼠标穿透失败", exc_info=True)

    def stop(self) -> None:
        self._timer.stop()
        try:
            _set_windows_click_through(int(self._window.winId()), False)
        except Exception:
            pass

