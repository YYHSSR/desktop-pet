#pragma once

#include "Geometry.hpp"
#include <algorithm>
#include <cmath>

namespace Pet::Core {

// 桌宠实体状态：位置 + 点击 Q 弹形变。
//
// 拖动物理（重力/惯性/边界反弹/弹弓弹射/拖拽甩出）已整体移除：
// 拖拽为直接跟随移动，松手停在原地。
// 点击 Q 弹与拖动物理无关（触发点在点击交互），因此保留形变能力。
class PetBody {
public:
    void setPosition(const Vector2D& pos) noexcept { m_position = pos; }
    [[nodiscard]] const Vector2D& position() const noexcept { return m_position; }

    // intensity <= 1.0 直接作为形变强度（点击 Q 弹传 0.4 产生明显果冻回弹）
    void triggerSquash(double intensity) noexcept {
        const double factor = std::clamp(intensity * 0.35, 0.0, 0.35);
        m_squashX = 1.0 + factor * 0.8;
        m_squashY = 1.0 - factor;
    }

    // 形变弹性恢复至 1.0（由控制器以约 16ms 周期驱动）
    void updateSquash(double dtSeconds) noexcept {
        const double t = (dtSeconds > 0.0 && dtSeconds <= 0.2) ? dtSeconds : 0.016;
        const double k = 0.15 * (t / 0.016);
        m_squashX += (1.0 - m_squashX) * k;
        m_squashY += (1.0 - m_squashY) * k;
    }

    [[nodiscard]] double squashScaleX() const noexcept { return m_squashX; }
    [[nodiscard]] double squashScaleY() const noexcept { return m_squashY; }
    [[nodiscard]] bool squashSettled() const noexcept {
        return std::abs(m_squashX - 1.0) < 0.001 && std::abs(m_squashY - 1.0) < 0.001;
    }
    void resetSquash() noexcept { m_squashX = 1.0; m_squashY = 1.0; }

private:
    Vector2D m_position{500.0, 500.0};
    double m_squashX{1.0};
    double m_squashY{1.0};
};

} // namespace Pet::Core
