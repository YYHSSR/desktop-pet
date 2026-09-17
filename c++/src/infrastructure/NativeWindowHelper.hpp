#pragma once

#include <QWindow>

namespace Pet::Infrastructure {

class NativeWindowHelper {
public:
    static bool isForegroundWindowFullscreen();
};

} // namespace Pet::Infrastructure
