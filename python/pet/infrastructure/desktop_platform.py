"""Application activation policy and Qt platform selection."""
import os
import sys

def _mac_set_dock_icon_visible(visible: bool) -> None:
    """Switch the macOS application policy without restarting the pet.

    The speech bubble itself owns the non-activating window flags; application
    activation policy must not be used as a focus workaround because Accessory
    Regular (0) displays a Dock item; Accessory (1) keeps the application out
    of the Dock. Pet tool windows own their independent visibility/focus flags.
    """
    if sys.platform != 'darwin':
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.restype = ctypes.c_void_p
        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        shared = msg(
            objc.objc_getClass(b'NSApplication'),
            objc.sel_registerName(b'sharedApplication'),
        )
        # NSApplicationActivationPolicyRegular = 0; Accessory = 1
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(shared, objc.sel_registerName(b'setActivationPolicy:'), 0 if visible else 1)
    except Exception:
        pass



def _default_xcb_platform_on_wayland() -> None:
    """Linux Wayland 会话下把 Qt 平台插件默认设为 xcb（XWayland）。

    Wayland 协议不允许客户端自行移动顶层窗口，桌宠拖动依赖的
    QWidget.move() 会被合成器静默忽略（表现为无法拖动）；透明无边框
    窗口在原生 wayland 插件下还存在重绘残留（拖影）。须在创建
    QApplication 之前调用。用户显式设置 QT_QPA_PLATFORM 时尊重其选择。
    """
    if not sys.platform.startswith("linux"):
        return
    if "QT_QPA_PLATFORM" in os.environ:
        return
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "xcb"

