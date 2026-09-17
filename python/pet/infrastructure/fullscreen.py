"""Foreground fullscreen detection; safe to call without a Qt event loop."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
from pet.infrastructure import vision as vision_mod
from pet.infrastructure.native_window import GWL_STYLE, GWL_EXSTYLE, _WS_CAPTION, _WS_EX_TOPMOST, _WinMonitorInfo

def _fullscreen_geometry_hit(l: float, t: float, r: float, b: float,
                             geom, has_caption: bool, topmost: bool = False) -> bool:
    """覆盖整屏几何，且（无标题栏 或 置顶）= 真全屏。

    判据组合的原因：
    - 带标题栏的普通/最大化窗口（含 Windows 自动隐藏任务栏场景）不置顶 → 排除；
    - 真全屏游戏/视频：多数去掉标题栏（无标题栏直接命中）；Unity/UE 系游戏
      （如绝区零）全屏时保留 WS_CAPTION 样式位但几乎必带 WS_EX_TOPMOST，用
      置顶位兜住；
    - 已最大化后按 F11 的窗口（IsZoomed 仍为真、标题栏被清掉）也正常命中。

    geom 兼容 QRect（方法访问）与 win32 RECT（属性访问）。
    """
    if has_caption and not topmost:
        return False
    gl = geom.left() if callable(getattr(geom, "left", None)) else geom.left
    gt = geom.top() if callable(getattr(geom, "top", None)) else geom.top
    gr = geom.right() if callable(getattr(geom, "right", None)) else geom.right
    gb = geom.bottom() if callable(getattr(geom, "bottom", None)) else geom.bottom
    return l <= gl and t <= gt and r >= gr and b >= gb


def _fs_user_busy_state() -> tuple[bool, int]:
    """SHQueryUserNotificationState：Windows 自报的全屏/演示忙状态。

    与几何判定互补——几何判定在 DPI 虚拟化、跨屏、DWM 边界差异下可能漏判，
    而这个 API 是 Windows 自己（Focus Assist/通知静默）判定"用户正在
    全屏"的依据，游戏和全屏视频都会触发。返回 (是否全屏忙, 原始状态值)。
    """
    if os.name != 'nt':
        return False, -1
    try:
        state = ctypes.c_int(0)
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:  # S_OK
            return False, -1
        # 2=QUNS_BUSY(全屏应用运行中) 3=QUNS_RUNNING_D3D_FULL_SCREEN 4=QUNS_PRESENTATION_MODE
        return state.value in (2, 3, 4), state.value
    except Exception:
        return False, -1


def probe_foreground_fullscreen(*, skip_classes, geometry_hit, busy_state) -> tuple[bool, str]:
    """前台窗口全屏探测，返回 (是否全屏, 诊断描述)。

    可在任意线程调用——不触碰 Qt 对象。判定链：
    1. foreground_window_info()（vision.py）：排除不可见/最小化/cloaked
       窗口，取 DWM 框架边界（物理像素，与本进程 DPI awareness 一致）；
    2. 排除本进程与 shell 窗口；
    3. 几何判定：窗口覆盖所在显示器完整几何（含任务栏），且无标题栏或置顶；
    4. 兜底判定：Windows SHQueryUserNotificationState 报告全屏忙状态。
    """
    if os.name != 'nt':
        return False, "非 Windows"
    u32 = ctypes.windll.user32
    # 句柄是 64 位指针：显式声明签名，避免 ctypes 默认 int32 截断
    u32.MonitorFromWindow.restype = wintypes.HANDLE
    u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    info = vision_mod.foreground_window_info()
    if not info:
        return False, "无可判定前台窗口(不可见/最小化/cloaked)"
    hwnd = info['hwnd']
    # 排除本进程与其他变体/多开的桌宠进程（置顶小窗，几何不会误判，
    # 但 SHQueryUserNotificationState 兜底需要进程名兜底排除）
    proc = info.get('process', '')
    if info.get('pid') == os.getpid() or proc.lower().startswith('desktop-pet'):
        return False, f"前台是桌宠自身 {proc}"
    # 排除桌面/任务栏等 shell 窗口
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    cls = buf.value
    if cls in skip_classes:
        return False, f"shell 窗口 {cls}"

    style = u32.GetWindowLongW(hwnd, GWL_STYLE)
    has_caption = bool(style & _WS_CAPTION)
    exstyle = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    topmost = bool(exstyle & _WS_EX_TOPMOST)
    x, y, w, h = info['rect']
    # 窗口所在显示器的完整几何（与 GetWindowRect/DWM 边界同为
    # 本进程 DPI awareness 下的坐标，天然一致）
    mon = u32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = _WinMonitorInfo()
    mi.cbSize = ctypes.sizeof(_WinMonitorInfo)
    if not u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False, f"GetMonitorInfoW 失败 cls={cls}"
    if geometry_hit(
            x, y, x + w, y + h, mi.rcMonitor, has_caption, topmost):
        return True, f"几何覆盖 cls={cls} proc={info.get('process', '')}"
    busy, bstate = busy_state()
    if busy:
        return True, (f"SHQueryUserNotificationState={bstate} "
                      f"cls={cls} proc={info.get('process', '')}")
    detail = (f"未命中 cls={cls} proc={info.get('process', '')} "
              f"caption={has_caption} topmost={topmost} "
              f"rect=({x},{y},{x + w},{y + h}) "
              f"monitor=({mi.rcMonitor.left},{mi.rcMonitor.top},"
              f"{mi.rcMonitor.right},{mi.rcMonitor.bottom}) busy={bstate}")
    return False, detail

