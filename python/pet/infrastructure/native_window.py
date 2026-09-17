"""Native window operations. No dependency on a concrete pet widget."""
from __future__ import annotations
import ctypes
import sys
from PySide6.QtCore import Qt

def _keep_macos_tool_window_visible(window) -> None:
    """Tool windows must remain visible while another application is active.

    This is independent from the configurable z-order. Without the attribute,
    Cocoa automatically hides a Qt.Tool window when the accessory application
    resigns active, which looked like the WebM Chat pet had exited.
    """
    if sys.platform == 'darwin':
        window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)


def _mac_set_window_level(view_id: int, level: int) -> bool:
    """macOS 原生：把 NSWindow 层级设为指定值（3=置顶浮动，0=普通）。

    Qt 的 WindowStaysOnTopHint 在 macOS 上对无边框 Tool 窗口/运行时切换不可靠，
    这里用 objc runtime 直接调 [NSWindow setLevel:] 强制生效（ctypes 零依赖）。

    只在真实 cocoa 平台执行：offscreen/minimal 等测试平台下 winId() 不是
    NSView 指针，objc_msgSend 会直接 SIGSEGV（无法被 try/except 捕获）。
    """
    if sys.platform != 'darwin':
        return False
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != 'cocoa':
            return False
    except Exception:
        return False
    try:
        import ctypes
        import ctypes.util

        lib_path = ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib'
        objc = ctypes.cdll.LoadLibrary(lib_path)

        # 关键：sel_registerName 返回 SEL（64 位指针）。ctypes 默认按 c_int(32 位)
        # 截断返回值，损坏的 SEL 会让 ObjC runtime 段错误（SIGSEGV），必须显式声明
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p

        sel_window = objc.sel_registerName(b'window')
        sel_set_level = objc.sel_registerName(b'setLevel:')
        sel_order_front = objc.sel_registerName(b'orderFrontRegardless')

        # [view window] —— 无参，返回 NSWindow*
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        window = msg(ctypes.c_void_p(view_id), sel_window)
        if not window:
            return False

        # [window setLevel:level] —— 一个 NSInteger 参数
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(ctypes.c_void_p(window), sel_set_level, level)
        if level > 0:
            # Changing WindowStaysOnTopHint recreates the NSWindow. Setting the
            # floating level alone may leave the replacement ordered behind
            # the currently active application until Cocoa's next ordering
            # pass; orderFrontRegardless commits the new level immediately.
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            msg(ctypes.c_void_p(window), sel_order_front)
        return True
    except Exception:
        return False


GWL_STYLE = -16             # GetWindowLongW：取窗口样式


GWL_EXSTYLE = -20           # GetWindowLongW：取扩展样式


_WS_CAPTION = 0x00C00000    # WS_BORDER | WS_DLGFRAME（带标题栏）


_WS_EX_TOPMOST = 0x00000008  # 置顶：真全屏游戏/视频几乎必带，普通最大化窗口不带


_WS_EX_TRANSPARENT = 0x00000020


class _WinRect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _WinMonitorInfo(ctypes.Structure):
    """GetMonitorInfoW 的 MONITORINFO（只读 rcMonitor：显示器完整几何，物理像素）。"""
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', _WinRect),
                ('rcWork', _WinRect), ('dwFlags', ctypes.c_ulong)]


def _set_windows_click_through(hwnd: int, enabled: bool, user32=None) -> bool:
    """切换 layered HWND 的输入穿透扩展样式。"""
    user32 = user32 or ctypes.windll.user32
    style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    updated = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
    if updated == style:
        return False
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, updated)
    return True

