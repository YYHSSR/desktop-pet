#pragma once

#include <cmath>

namespace Pet::Core {

struct Vector2D {
    double x = 0.0;
    double y = 0.0;

    constexpr Vector2D() = default;
    constexpr Vector2D(double px, double py) : x(px), y(py) {}

    constexpr Vector2D operator+(const Vector2D& other) const noexcept {
        return {x + other.x, y + other.y};
    }

    constexpr Vector2D operator-(const Vector2D& other) const noexcept {
        return {x - other.x, y - other.y};
    }

    constexpr Vector2D operator*(double scalar) const noexcept {
        return {x * scalar, y * scalar};
    }

    Vector2D& operator+=(const Vector2D& other) noexcept {
        x += other.x;
        y += other.y;
        return *this;
    }

    Vector2D& operator*=(double scalar) noexcept {
        x *= scalar;
        y *= scalar;
        return *this;
    }

    [[nodiscard]] double lengthSquared() const noexcept {
        return x * x + y * y;
    }

    [[nodiscard]] double length() const noexcept {
        return std::sqrt(lengthSquared());
    }

    [[nodiscard]] Vector2D normalized() const noexcept {
        double len = length();
        if (len > 1e-6) {
            return {x / len, y / len};
        }
        return {0.0, 0.0};
    }
};

struct Rect {
    double x = 0.0;
    double y = 0.0;
    double width = 0.0;
    double height = 0.0;

    [[nodiscard]] constexpr double left() const noexcept { return x; }
    [[nodiscard]] constexpr double right() const noexcept { return x + width; }
    [[nodiscard]] constexpr double top() const noexcept { return y; }
    [[nodiscard]] constexpr double bottom() const noexcept { return y + height; }
    [[nodiscard]] constexpr Vector2D center() const noexcept {
        return {x + width * 0.5, y + height * 0.5};
    }

    [[nodiscard]] bool intersects(const Rect& other) const noexcept {
        return !(left() > other.right() || right() < other.left() ||
                 top() > other.bottom() || bottom() < other.top());
    }
};

struct Circle {
    Vector2D center;
    double radius = 0.0;

    [[nodiscard]] bool intersects(const Circle& other) const noexcept {
        double distSq = (center - other.center).lengthSquared();
        double rSum = radius + other.radius;
        return distSq <= (rSum * rSum);
    }
};

} // namespace Pet::Core
