#include "MenuStyle.hpp"

#include <QApplication>
#include <QEvent>
#include <QFontDatabase>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPalette>
#include <QPen>
#include <QTimer>
#include <algorithm>
#include <cmath>

namespace Pet::UI {

namespace {

// Python common.py 的 SYSTEM_FONT_STACK
constexpr const char* kSystemFontStack = "\"SF Pro Text\", \".AppleSystemUIFont\", \"PingFang SC\"";

// Python common.py 的 ICON_COLOR
constexpr const char* kIconColor = "#55585c";

QString clampColor(const QString& value, const QString& fallback) {
    const QString trimmed = value.trimmed();
    if (trimmed.size() == 7 && trimmed.startsWith(QLatin1Char('#'))) {
        bool ok = true;
        trimmed.mid(1).toUInt(&ok, 16);
        if (ok) {
            return trimmed.toLower();
        }
    }
    return fallback;
}

double clampOpacity(double value) {
    if (!std::isfinite(value)) {
        return 0.94;
    }
    return std::max(0.72, std::min(1.0, value));
}

// 把 #RRGGBB 转 rgba(r, g, b, alpha)（Python 半透明分支）
QString hexToRgba(const QString& hex, double opacity) {
    if (!(hex.startsWith(QLatin1Char('#')) && hex.size() == 7)) {
        return hex;
    }
    const int red = hex.mid(1, 2).toInt(nullptr, 16);
    const int green = hex.mid(3, 2).toInt(nullptr, 16);
    const int blue = hex.mid(5, 2).toInt(nullptr, 16);
    return QStringLiteral("rgba(%1, %2, %3, %4)")
        .arg(red)
        .arg(green)
        .arg(blue)
        .arg(qRound(opacity * 255.0));
}

bool resolveDark(const MenuAppearance& appearance, const QMenu* menu) {
    if (appearance.theme == QStringLiteral("dark")) {
        return true;
    }
    if (appearance.theme != QStringLiteral("system") || menu == nullptr) {
        return false;
    }
    // Python: menu.palette().color(QPalette.Window).lightness() < 128
    return menu->palette().color(QPalette::Window).lightness() < 128;
}

void syncOverlayGeometry(QWidget* overlay, QMenu* menu) {
    if (menu == nullptr || overlay == nullptr) {
        return;
    }
    overlay->setGeometry(menu->rect());
    overlay->show();
    overlay->raise();
    overlay->update();
}

} // namespace

MenuAppearance normalizedAppearance(const MenuAppearance& raw) {
    MenuAppearance result = raw;
    if (result.theme != QStringLiteral("system") && result.theme != QStringLiteral("light")
        && result.theme != QStringLiteral("dark")) {
        result.theme = QStringLiteral("system");
    }
    if (result.density != QStringLiteral("compact") && result.density != QStringLiteral("standard")
        && result.density != QStringLiteral("spacious")) {
        result.density = QStringLiteral("standard");
    }
    result.cornerRadius = std::max(6, std::min(18, result.cornerRadius));
    result.uiFontSize = std::max(10, std::min(18, result.uiFontSize));
    if (result.uiFont.isEmpty()) {
        result.uiFont = QStringLiteral("system");
    }
    result.uiFont = result.uiFont.left(80);
    result.opacity = clampOpacity(result.opacity);
    result.lightBackground = clampColor(result.lightBackground, QStringLiteral("#ffffff"));
    result.lightForeground = clampColor(result.lightForeground, QStringLiteral("#171717"));
    result.lightHover = clampColor(result.lightHover, QStringLiteral("#eeeeee"));
    result.darkBackground = clampColor(result.darkBackground, QStringLiteral("#252525"));
    result.darkForeground = clampColor(result.darkForeground, QStringLiteral("#f3f3f3"));
    result.darkHover = clampColor(result.darkHover, QStringLiteral("#3a3a3a"));
    return result;
}

MenuAppearance menuAppearanceFromMap(const QVariantMap& values) {
    MenuAppearance appearance;
    if (values.isEmpty()) {
        return appearance;
    }
    appearance.theme = values.value(QStringLiteral("theme"), appearance.theme).toString();
    appearance.density = values.value(QStringLiteral("density"), appearance.density).toString();
    appearance.cornerRadius = values.value(QStringLiteral("corner_radius"), appearance.cornerRadius).toInt();
    appearance.uiFont = values.value(QStringLiteral("ui_font"), appearance.uiFont).toString();
    appearance.uiFontSize = values.value(QStringLiteral("ui_font_size"), appearance.uiFontSize).toInt();
    appearance.translucent = values.value(QStringLiteral("translucent"), appearance.translucent).toBool();
    appearance.opacity = values.value(QStringLiteral("opacity"), appearance.opacity).toDouble();
    appearance.lightBackground = values.value(QStringLiteral("light_background"), appearance.lightBackground).toString();
    appearance.lightForeground = values.value(QStringLiteral("light_foreground"), appearance.lightForeground).toString();
    appearance.lightHover = values.value(QStringLiteral("light_hover"), appearance.lightHover).toString();
    appearance.darkBackground = values.value(QStringLiteral("dark_background"), appearance.darkBackground).toString();
    appearance.darkForeground = values.value(QStringLiteral("dark_foreground"), appearance.darkForeground).toString();
    appearance.darkHover = values.value(QStringLiteral("dark_hover"), appearance.darkHover).toString();
    return normalizedAppearance(appearance);
}

QFont systemUiFont(int pixelSize, bool bold) {
    QFont font = QFontDatabase::systemFont(QFontDatabase::GeneralFont);
    font.setPixelSize(pixelSize);
    font.setWeight(bold ? QFont::Bold : QFont::Normal);
    return font;
}

QString systemFontStack() {
    return QString::fromLatin1(kSystemFontStack);
}

QString modernMenuStyleSheet(const MenuAppearance& appearance, bool dark) {
    const MenuAppearance cfg = normalizedAppearance(appearance);
    const QString density = cfg.density;
    const int radius = cfg.cornerRadius;

    int verticalPadding = 3;
    if (density == QStringLiteral("compact")) {
        verticalPadding = 2;
    } else if (density == QStringLiteral("spacious")) {
        verticalPadding = 5;
    }
    int separatorMargin = 4;
    if (density == QStringLiteral("compact")) {
        separatorMargin = 3;
    } else if (density == QStringLiteral("spacious")) {
        separatorMargin = 6;
    }

    QString background = dark ? cfg.darkBackground : cfg.lightBackground;
    const QString foreground = dark ? cfg.darkForeground : cfg.lightForeground;
    const QString selected = dark ? cfg.darkHover : cfg.lightHover;
    const QString selectedText = dark ? QStringLiteral("#ffffff") : QStringLiteral("#111111");
    const QString separator = dark ? QStringLiteral("#484848") : QStringLiteral("#e5e5e5");
    const QString disabled = dark ? QStringLiteral("#787878") : QStringLiteral("#9a9a9a");
    const int fontSize = cfg.uiFontSize;

    const QString requestedFont = QString(cfg.uiFont).remove(QLatin1Char('"'));
    const QString fontStack = requestedFont == QStringLiteral("system")
        ? QString::fromLatin1(kSystemFontStack)
        : QStringLiteral("\"%1\", %2").arg(requestedFont, QString::fromLatin1(kSystemFontStack));

    if (cfg.translucent) {
        background = hexToRgba(background, cfg.opacity);
    }

    return QStringLiteral(
               "QMenu {\n"
               "    background-color: %1;\n"
               "    color: %2;\n"
               "    border: none;\n"
               "    border-radius: %3px;\n"
               "    padding: 7px;\n"
               "    font-family: %4;\n"
               "    font-size: %5px;\n"
               "    icon-size: 18px;\n"
               "}\n"
               "QMenu::item {\n"
               "    min-height: 18px;\n"
               "    padding: %6px 29px %6px 13px;\n"
               "    margin: 0;\n"
               "    border: none;\n"
               "    border-radius: 9px;\n"
               "}\n"
               "QMenu::item:selected {\n"
               "    background-color: %7;\n"
               "    color: %8;\n"
               "}\n"
               "QMenu::item:disabled { color: %9; }\n"
               "QMenu::icon { left: 9px; }\n"
               "QMenu::indicator { width: 1px; height: 1px; image: none; }\n"
               "QMenu::separator {\n"
               "    height: 1px;\n"
               "    background: %10;\n"
               "    margin: %11px 4px;\n"
               "}\n"
               "QMenu::right-arrow { width: 7px; height: 11px; margin-right: 10px; }\n"
               "QMenu::scroller { height: 20px; background: %1; }\n")
        .arg(background, foreground)
        .arg(radius)
        .arg(fontStack)
        .arg(fontSize)
        .arg(verticalPadding)
        .arg(selected, selectedText, disabled, separator)
        .arg(separatorMargin);
}

// ---------------------------------------------------------------- ModernHairlineBorder

ModernHairlineBorder::ModernHairlineBorder(QMenu* parent)
    : QWidget(parent) {
    setObjectName(QStringLiteral("modernHairlineBorder"));
    setProperty("physicalPixelWidth", 1);
    setAttribute(Qt::WA_TransparentForMouseEvents, true);
    hide();
}

void ModernHairlineBorder::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    const qreal dpr = std::max(1.0, devicePixelRatioF());
    const qreal inset = 0.5 / dpr;
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setBrush(Qt::NoBrush);
    const bool dark = parent() != nullptr && parent()->property("modernDark").toBool();
    painter.setPen(QPen(QColor(dark ? QStringLiteral("#555555") : QStringLiteral("#d8d8d8")), 1.0 / dpr));
    const qreal radius = parent() != nullptr
        ? parent()->property("modernCornerRadius").toDouble()
        : 12.0;
    painter.drawRoundedRect(
        QRectF(inset, inset, width() - 2 * inset, height() - 2 * inset),
        radius, radius);
}

// ---------------------------------------------------------------- ModernCheckLayer

ModernCheckLayer::ModernCheckLayer(QMenu* parent)
    : QWidget(parent) {
    setObjectName(QStringLiteral("modernCheckLayer"));
    setAttribute(Qt::WA_TransparentForMouseEvents, true);
    hide();
}

void ModernCheckLayer::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    auto* menu = qobject_cast<QMenu*>(parentWidget());
    if (menu == nullptr) {
        return;
    }
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    for (QAction* action : menu->actions()) {
        if (!action->isCheckable() || !action->isChecked() || !action->isVisible()) {
            continue;
        }
        const QRect rect = menu->actionGeometry(action);
        if (rect.isEmpty()) {
            continue;
        }
        const QPointF center(width() - 21.0, rect.center().y());
        painter.setPen(Qt::NoPen);
        painter.setBrush(QColor(QStringLiteral("#1677e8")));
        painter.drawEllipse(QRectF(center.x() - 6.5, center.y() - 6.5, 13.0, 13.0));
        painter.setBrush(Qt::NoBrush);
        painter.setPen(QPen(QColor(QStringLiteral("#ffffff")), 1.55, Qt::SolidLine, Qt::RoundCap));
        painter.drawLine(QPointF(center.x() - 3.7, center.y()), QPointF(center.x() - 1.2, center.y() + 2.4));
        painter.drawLine(QPointF(center.x() - 1.2, center.y() + 2.4), QPointF(center.x() + 3.8, center.y() - 2.8));
    }
}

// ---------------------------------------------------------------- ResponsiveMenuStyle

int ResponsiveMenuStyle::styleHint(StyleHint hint, const QStyleOption* option,
                                   const QWidget* widget, QStyleHintReturn* returnData) const {
    switch (hint) {
    case QStyle::SH_Menu_Scrollable: return 1;
    case QStyle::SH_Menu_SubMenuPopupDelay: return 60;
    case QStyle::SH_Menu_SubMenuSloppyCloseTimeout: return 120;
    case QStyle::SH_Menu_SubMenuSloppySelectOtherActions: return 1;
    case QStyle::SH_Menu_SubMenuUniDirection: return 0;
    case QStyle::SH_Menu_SubMenuUniDirectionFailCount: return 1;
    default: break;
    }
    return QProxyStyle::styleHint(hint, option, widget, returnData);
}

// ---------------------------------------------------------------- StayOpenMenuFilter

bool StayOpenMenuFilter::eventFilter(QObject* watched, QEvent* event) {
    auto* menu = qobject_cast<QMenu*>(watched);
    if (menu != nullptr && event->type() == QEvent::MouseButtonRelease) {
        auto* mouseEvent = static_cast<QMouseEvent*>(event);
        if (mouseEvent->button() != Qt::LeftButton) {
            return false;
        }
        QAction* action = menu->actionAt(mouseEvent->pos());
        if (action == nullptr || action->isSeparator() || action->menu() != nullptr || !action->isEnabled()) {
            return false;
        }
        if (action->property("closeOnTrigger").toBool()) {
            return false;
        }
        action->trigger();
        event->accept();
        return true;
    }
    return false;
}

// ---------------------------------------------------------------- 安装函数

void applyModernMenuStyle(QMenu* menu, const MenuAppearance& appearance) {
    if (menu == nullptr) {
        return;
    }
    const MenuAppearance cfg = normalizedAppearance(appearance);
    const bool dark = resolveDark(cfg, menu);

    menu->setObjectName(QStringLiteral("modernContextMenu"));
    menu->setProperty("menuStyle", QStringLiteral("modern"));
    menu->setProperty("modernTheme", cfg.theme);
    menu->setProperty("modernDensity", cfg.density);
    menu->setProperty("modernCornerRadius", cfg.cornerRadius);
    menu->setProperty("modernDark", dark);
    // 供 inheritMenuStyle 逐字段继承（Python 的 modernAppearance 属性等价物）。
    menu->setProperty("modernAppearanceFull", QVariant::fromValue(cfg));
    menu->setAttribute(Qt::WA_TranslucentBackground, true);

    QFont font = systemUiFont(cfg.uiFontSize);
    if (cfg.uiFont != QStringLiteral("system")) {
        font.setFamily(cfg.uiFont);
    }
    menu->setFont(font);
    menu->setStyleSheet(modernMenuStyleSheet(cfg, dark));

    auto* border = new ModernHairlineBorder(menu);
    QObject::connect(menu, &QMenu::aboutToShow, border, [menu, border]() {
        QTimer::singleShot(0, menu, [menu, border]() {
            syncOverlayGeometry(border, menu);
        });
    });
}

void inheritMenuStyle(QMenu* parent, QMenu* submenu) {
    if (parent == nullptr || submenu == nullptr) {
        return;
    }
    const QString styleId = parent->property("menuStyle").toString();
    if (styleId != QStringLiteral("modern")) {
        submenu->setFont(parent->font());
        return;
    }

    MenuAppearance appearance;
    appearance.theme = parent->property("modernTheme").toString();
    if (appearance.theme.isEmpty()) {
        appearance.theme = QStringLiteral("system");
    }
    appearance.density = parent->property("modernDensity").toString();
    if (appearance.density.isEmpty()) {
        appearance.density = QStringLiteral("standard");
    }
    const QVariant radius = parent->property("modernCornerRadius");
    appearance.cornerRadius = radius.isValid() ? radius.toInt() : 12;

    // 父菜单若携带完整外观（来自 applyModernMenuStyle 的调用方），逐字段继承。
    const MenuAppearance full = parent->property("modernAppearanceFull").value<MenuAppearance>();
    appearance.uiFont = full.uiFont;
    appearance.uiFontSize = full.uiFontSize;
    appearance.translucent = full.translucent;
    appearance.opacity = full.opacity;
    appearance.lightBackground = full.lightBackground;
    appearance.lightForeground = full.lightForeground;
    appearance.lightHover = full.lightHover;
    appearance.darkBackground = full.darkBackground;
    appearance.darkForeground = full.darkForeground;
    appearance.darkHover = full.darkHover;

    applyModernMenuStyle(submenu, appearance);
}

void installResponsiveMenuStyle(QMenu* menu) {
    if (menu == nullptr) {
        return;
    }
    for (QAction* action : menu->actions()) {
        if (QMenu* sub = action->menu()) {
            installResponsiveMenuStyle(sub);
        }
    }
    auto* style = new ResponsiveMenuStyle();
    style->setParent(menu);
    menu->setStyle(style);
}

void installStayOpenFilter(QMenu* menu) {
    if (menu == nullptr) {
        return;
    }
    for (QAction* action : menu->actions()) {
        if (QMenu* sub = action->menu()) {
            installStayOpenFilter(sub);
        }
    }
    menu->installEventFilter(new StayOpenMenuFilter(menu));
}

void installModernCheckIndicators(QMenu* menu) {
    if (menu == nullptr) {
        return;
    }
    menu->setProperty("paintChecksOnRight", true);
    auto* layer = new ModernCheckLayer(menu);

    QObject::connect(menu, &QMenu::aboutToShow, layer, [menu, layer]() {
        QTimer::singleShot(0, menu, [menu, layer]() {
            syncOverlayGeometry(layer, menu);
        });
    });
    QObject::connect(menu, &QMenu::aboutToHide, layer, &QWidget::hide);

    for (QAction* action : menu->actions()) {
        if (QMenu* sub = action->menu()) {
            installModernCheckIndicators(sub);
        }
        if (action->isCheckable()) {
            QObject::connect(action, &QAction::toggled, layer, [layer](bool) {
                layer->update();
            });
        }
    }
}

bool menuIsDark(const QMenu* menu) {
    return menu != nullptr && menu->property("modernDark").toBool();
}

} // namespace Pet::UI
