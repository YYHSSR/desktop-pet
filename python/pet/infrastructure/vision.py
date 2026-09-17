# -*- coding: utf-8 -*-
"""前台窗口状态感知。

仅保留被系统集成功能使用的最小能力：
- :func:`foreground_window_info` / :func:`get_foreground_window_rect`
  供全屏自动隐藏（``pet.infrastructure.fullscreen``）判断前台是否为全屏应用。

本模块零网络请求、零截图落盘。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from pathlib import Path


def foreground_window_info() -> dict | None:
    """获取前台窗口详细信息：{hwnd, pid, process, title, rect(x,y,w,h)}。
    若窗口不可见/最小化/被 cloaked 或获取失败，返回 None。"""
    if sys.platform != 'win32':
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
        user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
        user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
        user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
        user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return None

        cloaked = ctypes.c_int(0)
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        if dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        ) == 0 and cloaked.value != 0:
            return None

        rect_dwm = ctypes.wintypes.RECT()
        rect: tuple[int, int, int, int] | None = None
        if dwmapi.DwmGetWindowAttribute(
            hwnd, 9, ctypes.byref(rect_dwm), ctypes.sizeof(rect_dwm)
        ) == 0:
            w = rect_dwm.right - rect_dwm.left
            h = rect_dwm.bottom - rect_dwm.top
            if w > 0 and h > 0:
                rect = (rect_dwm.left, rect_dwm.top, w, h)

        if rect is None:
            rect_raw = ctypes.wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect_raw)):
                w = rect_raw.right - rect_raw.left
                h = rect_raw.bottom - rect_raw.top
                if w > 0 and h > 0:
                    rect = (rect_raw.left, rect_raw.top, w, h)

        if rect is None:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        title = ''
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()

        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ''
        if pid.value:
            hproc = kernel32.OpenProcess(0x1000, False, pid.value)
            if hproc:
                try:
                    pbuf = ctypes.create_unicode_buffer(260)
                    size = ctypes.c_ulong(260)
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                        proc = Path(pbuf.value).name
                finally:
                    kernel32.CloseHandle(hproc)

        return {
            'hwnd': hwnd,
            'pid': pid.value,
            'process': proc,
            'title': title,
            'rect': rect,
        }
    except Exception:
        return None


def get_foreground_window_rect() -> tuple[int, int, int, int] | None:
    """获取前台窗口矩形 (x, y, w, h)；拿不到返回 None。"""
    info = foreground_window_info()
    return info['rect'] if info else None
