#include "NativeWindowHelper.hpp"

#ifdef _WIN32
#include <windows.h>
#include <dwmapi.h>
#endif

namespace Pet::Infrastructure {

bool NativeWindowHelper::isForegroundWindowFullscreen() {
#ifdef _WIN32
    HWND fg = GetForegroundWindow();
    if (!fg || fg == GetDesktopWindow() || fg == GetShellWindow()) {
        return false;
    }
    // 排除桌面背景、外壳工作区、任务栏与开始菜单层 (与 Python _FS_SKIP_CLASSES 1:1 对齐)
    wchar_t clsName[256] = {0};
    if (GetClassNameW(fg, clsName, 255) > 0) {
        std::wstring name(clsName);
        if (name == L"Progman" || name == L"WorkerW" ||
            name == L"Shell_TrayWnd" || name == L"Shell_SecondaryTrayWnd" ||
            name == L"Windows.UI.Core.CoreWindow") {
            return false;
        }
    }
    LONG style = GetWindowLong(fg, GWL_STYLE);
    // 如果窗口具有标准标题栏 (WS_CAPTION)，那只是常规最大化窗口，绝不隐藏桌宠
    if (style & WS_CAPTION) {
        return false;
    }
    RECT wRect;
    if (!GetWindowRect(fg, &wRect)) {
        return false;
    }
    int screenW = GetSystemMetrics(SM_CXSCREEN);
    int screenH = GetSystemMetrics(SM_CYSCREEN);
    return (wRect.left <= 0 && wRect.top <= 0 &&
            wRect.right >= screenW && wRect.bottom >= screenH);
#else
    return false;
#endif
}

} // namespace Pet::Infrastructure
