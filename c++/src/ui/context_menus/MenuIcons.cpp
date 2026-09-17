#include "MenuIcons.hpp"

#include <QBitmap>
#include <QFileIconProvider>
#include <QFileInfo>
#include <QHash>
#include <QPainter>
#include <QPainterPath>
#include <QPalette>
#include <QPen>
#include <QPolygonF>
#include <QRegion>
#include <QStyle>
#include <QtMath>
#include <algorithm>
#include <cmath>
#include <memory>
#include <vector>

namespace Pet::UI {

namespace {

// Python icons.py 的 _icon_theme(widget)
struct IconTheme {
    QString style;
    bool dark = false;
};

IconTheme resolveIconTheme(const QWidget* widget) {
    IconTheme theme;
    const QWidget* current = widget;
    while (current != nullptr) {
        const QString value = current->property("menuStyle").toString();
        if (!value.isEmpty()) {
            theme.style = value;
        }
        if (current->property("modernDark").toBool()) {
            theme.dark = true;
        }
        if (!theme.style.isEmpty() && theme.dark) {
            break;
        }
        current = current->parentWidget();
    }
    return theme;
}

qreal widgetDpr(const QWidget* widget) {
    if (widget == nullptr) {
        return 1.0;
    }
    const qreal dpr = widget->devicePixelRatioF();
    return dpr > 0.0 ? dpr : 1.0;
}

// Python icons.py 的 _new_icon_canvas(widget, requested_size)
struct IconCanvas {
    QPixmap pixmap;
    std::unique_ptr<QPainter> painter;
    QColor color;
};

IconCanvas makeIconCanvas(const QWidget* widget, int requestedSize) {
    const int size = requestedSize > 0 ? requestedSize : MenuIcons::smallIconSize(widget);
    const qreal dpr = widgetDpr(widget);

    IconCanvas canvas;
    canvas.pixmap = QPixmap(std::max(1, qRound(size * dpr)), std::max(1, qRound(size * dpr)));
    canvas.pixmap.setDevicePixelRatio(dpr);
    canvas.pixmap.fill(Qt::transparent);

    canvas.painter = std::make_unique<QPainter>(&canvas.pixmap);
    canvas.painter->setRenderHint(QPainter::Antialiasing, true);
    canvas.painter->scale(size / 16.0, size / 16.0);

    const IconTheme theme = resolveIconTheme(widget);
    if (theme.style == QStringLiteral("modern")) {
        canvas.color = QColor(theme.dark ? QStringLiteral("#d6d6d6") : QStringLiteral("#595959"));
    } else if (widget != nullptr) {
        canvas.color = widget->palette().color(widget->foregroundRole());
    } else {
        canvas.color = QColor(QStringLiteral("#595959"));
    }
    canvas.painter->setPen(
        QPen(canvas.color, 1.35, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    canvas.painter->setBrush(Qt::NoBrush);
    return canvas;
}

QPixmap renderVectorPixmap(const QWidget* widget, const QString& name, int requestedSize) {
    IconCanvas canvas = makeIconCanvas(widget, requestedSize);
    QPainter& p = *canvas.painter;
    const QColor color = canvas.color;
    QPainterPath path;

    if (name == QStringLiteral("search")) {
        p.drawEllipse(QPointF(6.5, 6.5), 4.5, 4.5);
        p.drawLine(QPointF(9.8, 9.8), QPointF(14.0, 14.0));
    } else if (name == QStringLiteral("chat")) {
        p.drawRoundedRect(QRectF(1.5, 2.0, 13.0, 9.5), 2.4, 2.4);
        path.moveTo(5.2, 11.2);
        path.lineTo(4.0, 14.0);
        path.lineTo(7.4, 11.5);
        p.drawPath(path);
        p.drawLine(QPointF(4.6, 6.7), QPointF(11.4, 6.7));
    } else if (name == QStringLiteral("agent") || name == QStringLiteral("agent_link")
               || name == QStringLiteral("robot")) {
        p.drawLine(QPointF(8.0, 1.8), QPointF(8.0, 3.8));
        p.drawEllipse(QPointF(8.0, 1.4), 0.7, 0.7);
        p.drawRoundedRect(QRectF(2.8, 3.8, 10.4, 8.4), 2.0, 2.0);
        p.drawLine(QPointF(1.4, 7.2), QPointF(2.8, 7.2));
        p.drawLine(QPointF(13.2, 7.2), QPointF(14.6, 7.2));
        p.setBrush(QBrush(color));
        p.drawEllipse(QPointF(5.8, 7.5), 0.9, 0.9);
        p.drawEllipse(QPointF(10.2, 7.5), 0.9, 0.9);
        p.setBrush(Qt::NoBrush);
        p.drawLine(QPointF(5.6, 10.0), QPointF(10.4, 10.0));
    } else if (name == QStringLiteral("hide")) {
        p.drawEllipse(QPointF(8.0, 8.0), 6.0, 3.8);
        p.drawEllipse(QPointF(8.0, 8.0), 1.5, 1.5);
        p.drawLine(QPointF(2.5, 13.5), QPointF(13.5, 2.5));
    } else if (name == QStringLiteral("balance")) {
        p.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0);
        p.drawLine(QPointF(5.0, 5.5), QPointF(11.0, 5.5));
        p.drawLine(QPointF(8.0, 4.0), QPointF(8.0, 12.0));
        p.drawArc(QRectF(5.2, 5.0, 5.6, 6.0), 70 * 16, 220 * 16);
    } else if (name == QStringLiteral("update")) {
        p.drawArc(QRectF(2.0, 2.0, 12.0, 12.0), 35 * 16, 285 * 16);
        p.drawPolygon(QPolygonF{QPointF(10.7, 1.8), QPointF(14.0, 2.5), QPointF(12.0, 5.3)});
    } else if (name == QStringLiteral("download")) {
        p.drawLine(QPointF(8.0, 2.0), QPointF(8.0, 10.0));
        p.drawLine(QPointF(4.8, 7.0), QPointF(8.0, 10.2));
        p.drawLine(QPointF(11.2, 7.0), QPointF(8.0, 10.2));
        p.drawLine(QPointF(3.0, 13.5), QPointF(13.0, 13.5));
    } else if (name == QStringLiteral("settings")) {
        QPolygonF gear;
        for (int index = 0; index < 24; ++index) {
            const qreal angle = -M_PI / 2 + index * M_PI / 12;
            const qreal radius = (index % 3 == 1) ? 6.2 : 5.15;
            gear.append(QPointF(8.0 + std::cos(angle) * radius, 8.0 + std::sin(angle) * radius));
        }
        p.drawPolygon(gear);
        p.drawEllipse(QPointF(8.0, 8.0), 2.15, 2.15);
    } else if (name == QStringLiteral("play")) {
        p.drawPolygon(QPolygonF{QPointF(4.0, 2.5), QPointF(13.0, 8.0), QPointF(4.0, 13.5)});
    } else if (name == QStringLiteral("speed")) {
        p.drawArc(QRectF(2.0, 3.0, 12.0, 12.0), 0, 180 * 16);
        p.drawLine(QPointF(8.0, 9.0), QPointF(11.6, 5.6));
        p.setBrush(QBrush(color));
        p.drawEllipse(QPointF(8.0, 9.0), 1.0, 1.0);
        p.setBrush(Qt::NoBrush);
        p.drawLine(QPointF(3.0, 11.8), QPointF(13.0, 11.8));
    } else if (name == QStringLiteral("character")) {
        p.drawEllipse(QPointF(8.0, 5.0), 2.8, 2.8);
        p.drawArc(QRectF(2.8, 8.0, 10.4, 6.0), 18 * 16, 144 * 16);
    } else if (name == QStringLiteral("corner")) {
        p.drawLine(QPointF(13.5, 4.0), QPointF(13.5, 13.5));
        p.drawLine(QPointF(4.0, 13.5), QPointF(13.5, 13.5));
        p.drawLine(QPointF(3.0, 3.0), QPointF(10.5, 10.5));
        p.drawLine(QPointF(6.5, 10.5), QPointF(10.5, 10.5));
        p.drawLine(QPointF(10.5, 6.5), QPointF(10.5, 10.5));
    } else if (name == QStringLiteral("pin")) {
        p.drawRoundedRect(QRectF(4.5, 2.0, 7.0, 5.0), 1.0, 1.0);
        p.drawLine(QPointF(5.5, 7.0), QPointF(3.5, 10.0));
        p.drawLine(QPointF(10.5, 7.0), QPointF(12.5, 10.0));
        p.drawLine(QPointF(3.5, 10.0), QPointF(12.5, 10.0));
        p.drawLine(QPointF(8.0, 10.0), QPointF(8.0, 14.0));
    } else if (name == QStringLiteral("pause")) {
        p.drawRoundedRect(QRectF(3.5, 2.5, 3.2, 11.0), 0.7, 0.7);
        p.drawRoundedRect(QRectF(9.3, 2.5, 3.2, 11.0), 0.7, 0.7);
    } else if (name == QStringLiteral("autostart")) {
        p.drawArc(QRectF(2.0, 2.0, 12.0, 12.0), 38 * 16, 282 * 16);
        p.setBrush(QBrush(color));
        p.drawPolygon(QPolygonF{QPointF(10.7, 1.9), QPointF(14.0, 2.5), QPointF(12.1, 5.3)});
        p.setBrush(Qt::NoBrush);
    } else if (name == QStringLiteral("size")) {
        p.drawLine(QPointF(3.0, 13.0), QPointF(13.0, 3.0));
        p.drawLine(QPointF(3.0, 8.5), QPointF(3.0, 13.0));
        p.drawLine(QPointF(7.5, 13.0), QPointF(3.0, 13.0));
        p.drawLine(QPointF(8.5, 3.0), QPointF(13.0, 3.0));
        p.drawLine(QPointF(13.0, 3.0), QPointF(13.0, 7.5));
    } else if (name == QStringLiteral("web")) {
        p.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0);
        p.drawEllipse(QPointF(8.0, 8.0), 2.7, 6.0);
        p.drawLine(QPointF(2.3, 8.0), QPointF(13.7, 8.0));
    } else if (name == QStringLiteral("template")) {
        p.drawRoundedRect(QRectF(1.5, 2.0, 5.0, 12.0), 1.0, 1.0);
        p.drawRoundedRect(QRectF(8.0, 2.0, 6.5, 5.0), 1.0, 1.0);
        p.drawRoundedRect(QRectF(8.0, 8.5, 6.5, 5.5), 1.0, 1.0);
    } else if (name == QStringLiteral("application") || name == QStringLiteral("launcher")) {
        p.drawRoundedRect(QRectF(2.0, 2.0, 12.0, 12.0), 2.0, 2.0);
        const std::vector<QPointF> dots = {
            QPointF(5.0, 5.0), QPointF(11.0, 5.0), QPointF(5.0, 11.0), QPointF(11.0, 11.0)};
        for (const QPointF& dot : dots) {
            p.drawEllipse(dot, 1.15, 1.15);
        }
    } else if (name == QStringLiteral("appearance")) {
        p.drawEllipse(QPointF(8.0, 8.0), 5.8, 5.8);
        p.drawArc(QRectF(4.0, 4.0, 8.0, 8.0), 90 * 16, 180 * 16);
        p.drawLine(QPointF(8.0, 2.2), QPointF(8.0, 13.8));
    } else if (name == QStringLiteral("back") || name == QStringLiteral("save")) {
        p.drawLine(QPointF(13.5, 8.0), QPointF(3.0, 8.0));
        p.drawLine(QPointF(3.0, 8.0), QPointF(7.0, 4.0));
        p.drawLine(QPointF(3.0, 8.0), QPointF(7.0, 12.0));
    } else if (name == QStringLiteral("minimize")) {
        p.drawLine(QPointF(3.0, 11.5), QPointF(13.0, 11.5));
    } else if (name == QStringLiteral("clear")) {
        p.drawLine(QPointF(3.0, 12.5), QPointF(13.0, 12.5));
        p.drawLine(QPointF(5.0, 12.5), QPointF(5.0, 5.0));
        p.drawLine(QPointF(11.0, 12.5), QPointF(11.0, 5.0));
        p.drawLine(QPointF(3.8, 5.0), QPointF(12.2, 5.0));
        p.drawLine(QPointF(6.0, 2.8), QPointF(10.0, 2.8));
    } else if (name == QStringLiteral("add")) {
        p.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0);
        p.drawLine(QPointF(8.0, 4.5), QPointF(8.0, 11.5));
        p.drawLine(QPointF(4.5, 8.0), QPointF(11.5, 8.0));
    } else if (name == QStringLiteral("remove")) {
        p.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 13.0));
        p.drawLine(QPointF(13.0, 3.0), QPointF(3.0, 13.0));
    } else if (name == QStringLiteral("more")) {
        p.setBrush(QBrush(color));
        const qreal xs[3] = {4.0, 8.0, 12.0};
        for (qreal x : xs) {
            p.drawEllipse(QPointF(x, 8.0), 0.9, 0.9);
        }
        p.setBrush(Qt::NoBrush);
    } else if (name == QStringLiteral("multi_select")) {
        p.drawEllipse(QPointF(4.0, 4.5), 1.5, 1.5);
        p.drawEllipse(QPointF(4.0, 11.5), 1.5, 1.5);
        p.drawLine(QPointF(7.5, 4.5), QPointF(13.5, 4.5));
        p.drawLine(QPointF(7.5, 11.5), QPointF(13.5, 11.5));
    } else if (name == QStringLiteral("rename") || name == QStringLiteral("edit")) {
        p.drawRoundedRect(QRectF(2.2, 2.0, 8.3, 11.5), 1.2, 1.2);
        p.drawLine(QPointF(4.2, 5.0), QPointF(8.2, 5.0));
        p.drawLine(QPointF(4.2, 7.4), QPointF(7.0, 7.4));
        p.setBrush(QBrush(color));
        p.drawPolygon(QPolygonF{
            QPointF(6.8, 12.8), QPointF(7.5, 10.2),
            QPointF(12.6, 5.1), QPointF(14.2, 6.7), QPointF(9.1, 11.8),
        });
        p.setBrush(Qt::NoBrush);
    } else if (name == QStringLiteral("copy")) {
        p.drawRoundedRect(QRectF(5.0, 3.0, 8.0, 9.0), 1.3, 1.3);
        p.drawRoundedRect(QRectF(2.5, 5.5, 8.0, 8.0), 1.3, 1.3);
    } else if (name == QStringLiteral("retry") || name == QStringLiteral("refresh")) {
        p.drawArc(QRectF(2.4, 2.4, 11.2, 11.2), 35 * 16, 285 * 16);
        p.drawLine(QPointF(10.8, 2.8), QPointF(13.8, 3.2));
        p.drawLine(QPointF(13.8, 3.2), QPointF(12.4, 6.0));
    } else if (name == QStringLiteral("thumbs_up") || name == QStringLiteral("thumbs_down")) {
        if (name == QStringLiteral("thumbs_down")) {
            p.scale(1.0, -1.0);
            p.translate(0, -16);
        }
        path.moveTo(3.0, 7.0);
        path.lineTo(6.0, 7.0);
        path.lineTo(8.2, 3.0);
        path.cubicTo(8.8, 1.9, 10.1, 2.5, 10.0, 3.8);
        path.lineTo(9.8, 6.0);
        path.lineTo(13.0, 6.0);
        path.lineTo(12.0, 13.0);
        path.lineTo(6.0, 13.0);
        path.lineTo(3.0, 11.5);
        path.closeSubpath();
        p.drawPath(path);
    } else if (name == QStringLiteral("attach")) {
        path.moveTo(5.1, 8.9);
        path.lineTo(9.5, 4.5);
        path.cubicTo(12.2, 1.8, 15.1, 5.0, 12.7, 7.4);
        path.lineTo(7.0, 13.1);
        path.cubicTo(3.0, 17.1, -0.7, 12.3, 2.5, 9.1);
        path.lineTo(8.1, 3.5);
        p.drawPath(path);
    } else if (name == QStringLiteral("send")) {
        p.drawPolygon(QPolygonF{
            QPointF(2.0, 8.0), QPointF(13.5, 2.5), QPointF(10.8, 13.5), QPointF(7.7, 9.0),
        });
        p.drawLine(QPointF(2.0, 8.0), QPointF(7.7, 9.0));
        p.drawLine(QPointF(7.7, 9.0), QPointF(13.5, 2.5));
    } else if (name == QStringLiteral("stop")) {
        p.drawRoundedRect(QRectF(4.0, 4.0, 8.0, 8.0), 1.3, 1.3);
    } else if (name == QStringLiteral("sidebar")) {
        p.drawRoundedRect(QRectF(2.0, 2.5, 12.0, 11.0), 2.0, 2.0);
        p.drawLine(QPointF(6.1, 2.8), QPointF(6.1, 13.2));
        p.drawLine(QPointF(3.8, 5.5), QPointF(4.7, 5.5));
        p.drawLine(QPointF(3.8, 8.0), QPointF(4.7, 8.0));
    } else if (name == QStringLiteral("tools") || name == QStringLiteral("functions")) {
        p.drawRoundedRect(QRectF(1.5, 2.0, 13.0, 12.0), 2.0, 2.0);
        p.drawLine(QPointF(5.0, 5.0), QPointF(11.0, 11.0));
        p.drawEllipse(QPointF(4.5, 4.5), 1.5, 1.5);
        p.drawEllipse(QPointF(11.5, 11.5), 1.5, 1.5);
    } else if (name == QStringLiteral("speaker") || name == QStringLiteral("music")) {
        path.moveTo(3.0, 6.0);
        path.lineTo(6.0, 6.0);
        path.lineTo(9.5, 3.0);
        path.lineTo(9.5, 13.0);
        path.lineTo(6.0, 10.0);
        path.lineTo(3.0, 10.0);
        path.closeSubpath();
        p.drawPath(path);
        p.drawArc(QRectF(10.0, 5.5, 4.0, 5.0), -60 * 16, 120 * 16);
        p.drawArc(QRectF(9.0, 3.5, 7.0, 9.0), -60 * 16, 120 * 16);
    } else if (name == QStringLiteral("exit")) {
        p.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 13.0));
        p.drawLine(QPointF(13.0, 3.0), QPointF(3.0, 13.0));
    } else {
        // Python 的兜底分支：未识别名称画一个圆（loading 占位图标同样走这里）。
        p.drawEllipse(QPointF(8.0, 8.0), 5.5, 5.5);
    }

    p.end();
    canvas.painter.reset();
    return canvas.pixmap;
}

} // namespace

int MenuIcons::smallIconSize(const QWidget* widget) {
    const IconTheme theme = resolveIconTheme(widget);
    if (theme.style == QStringLiteral("modern")) {
        return 18;
    }
    if (widget == nullptr) {
        return 18;
    }
    auto* self = const_cast<QWidget*>(widget);
    const int metric = self->style()->pixelMetric(QStyle::PM_SmallIconSize, nullptr, self);
    return std::max(12, metric);
}

QIcon MenuIcons::vectorMenuIcon(const QWidget* widget, const QString& name, int size) {
    return QIcon(renderVectorPixmap(widget, name, size));
}

QIcon MenuIcons::fittedPetPixmapIcon(const QWidget* widget, const QPixmap& source) {
    const int size = smallIconSize(widget);
    const qreal dpr = widgetDpr(widget);
    if (source.isNull()) {
        return QIcon();
    }

    QPixmap trimmed = source;
    // 帧像素可能带 DPR=2 元数据，按点绘制会被解释为一半尺寸。
    trimmed.setDevicePixelRatio(1.0);

    // 动画帧使用完整视频画布，直接缩放会让角色只剩几个像素；
    // 先裁掉透明边距，与运行时桌宠图标一致。
    const QImage image = trimmed.toImage();
    const QRegion region(QBitmap::fromImage(image.createAlphaMask()));
    const QRect bounds = region.boundingRect();
    if (bounds.isValid() && !bounds.isEmpty()) {
        trimmed = QPixmap::fromImage(image.copy(bounds));
    }

    QPixmap canvas(std::max(1, qRound(size * dpr)), std::max(1, qRound(size * dpr)));
    canvas.setDevicePixelRatio(dpr);
    canvas.fill(Qt::transparent);

    QPainter painter(&canvas);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
    const qreal extent = size * 1.04;
    const qreal ratio = std::min(extent / trimmed.width(), extent / trimmed.height());
    const qreal width = trimmed.width() * ratio;
    const qreal height = trimmed.height() * ratio;
    const QRectF target((size - width) / 2.0, (size - height) / 2.0 - 0.2, width, height);
    painter.drawPixmap(target, trimmed, QRectF(0, 0, trimmed.width(), trimmed.height()));
    painter.end();
    return QIcon(canvas);
}

QIcon MenuIcons::fittedApplicationIcon(const QIcon& icon, int size, const QWidget* widget) {
    if (icon.isNull()) {
        return icon;
    }
    const qreal dpr = std::max(1.0, widgetDpr(widget));
    const int sourceSize = std::max(32, qRound(size * dpr * 2));
    QPixmap source = icon.pixmap(sourceSize, sourceSize);
    source.setDevicePixelRatio(1.0);
    const QRect bounds = QRegion(source.mask()).boundingRect();
    if (bounds.isEmpty()) {
        return icon;
    }

    QPixmap canvas(std::max(1, qRound(size * dpr)), std::max(1, qRound(size * dpr)));
    canvas.setDevicePixelRatio(dpr);
    canvas.fill(Qt::transparent);

    QPainter painter(&canvas);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
    painter.drawPixmap(
        QRectF(0.5, 0.5, size - 1.0, size - 1.0),
        source,
        QRectF(bounds));
    painter.end();
    return QIcon(canvas);
}

QIcon MenuIcons::quickAppIcon(const QWidget* widget, const QString& kind, const QString& path) {
    // 首次 QFileIconProvider 取应用图标可能较慢；按 (kind, path) 缓存结果。
    static QHash<QString, QIcon> cache;

    const QString cacheKey = kind + QLatin1Char('\n') + path;
    const auto cached = cache.constFind(cacheKey);
    if (cached != cache.constEnd()) {
        return cached.value();
    }

    QIcon icon;
    if (kind == QStringLiteral("default_browser")) {
        icon = vectorMenuIcon(widget, QStringLiteral("web"));
    } else if (!path.isEmpty()) {
        icon = QFileIconProvider().icon(QFileInfo(path));
        if (!icon.isNull()) {
            icon = fittedApplicationIcon(icon, 18, widget);
        } else {
            icon = vectorMenuIcon(widget, QStringLiteral("application"));
        }
    } else {
        icon = vectorMenuIcon(widget, QStringLiteral("application"));
    }

    cache.insert(cacheKey, icon);
    return icon;
}

} // namespace Pet::UI
