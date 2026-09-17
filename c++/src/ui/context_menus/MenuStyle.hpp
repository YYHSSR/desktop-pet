#pragma once

#include <QColor>
#include <QFont>
#include <QMenu>
#include <QMetaType>
#include <QObject>
#include <QProxyStyle>
#include <QString>
#include <QVariantMap>
#include <QWidget>

namespace Pet::UI {

// 对应 Python pet/ui/context_menus/menu_styles/modern.py 的 appearance dict。
// 所有默认值与 clamp 区间必须与 Python 保持一致。
struct MenuAppearance {
    QString theme = QStringLiteral("system");        // system | light | dark
    QString density = QStringLiteral("standard");     // compact | standard | spacious
    int cornerRadius = 12;                            // clamp 6..18
    QString uiFont = QStringLiteral("system");        // "system" 时使用默认字体栈
    int uiFontSize = 13;                              // clamp 10..18
    bool translucent = true;
    double opacity = 0.94;                            // clamp 0.72..1.0
    QString lightBackground = QStringLiteral("#ffffff");
    QString lightForeground = QStringLiteral("#171717");
    QString lightHover = QStringLiteral("#eeeeee");
    QString darkBackground = QStringLiteral("#252525");
    QString darkForeground = QStringLiteral("#f3f3f3");
    QString darkHover = QStringLiteral("#3a3a3a");
};

// 应用 1:1 移植自 Python 的 clamp 规则，保证外部（含手改 config.json）取到的值安全。
MenuAppearance normalizedAppearance(const MenuAppearance& raw);

// 从 ConfigManager::menuAppearanceValues() 的结果构造外观结构。
MenuAppearance menuAppearanceFromMap(const QVariantMap& values);

// 系统字体（Python system_ui_font：QFontDatabase.systemFont(GeneralFont) 后设像素字号）。
QFont systemUiFont(int pixelSize, bool bold = false);

// Python menu_styles/common.py 的 SYSTEM_FONT_STACK
QString systemFontStack();

// Python modern_menu_stylesheet(appearance, dark=...)：逐行 1:1 迁移。
QString modernMenuStyleSheet(const MenuAppearance& appearance, bool dark);

// Python menu_styles/modern.py 的 ModernHairlineBorder：稳定的 1 物理像素圆角描边。
class ModernHairlineBorder : public QWidget {
    Q_OBJECT
public:
    explicit ModernHairlineBorder(QMenu* parent);

protected:
    void paintEvent(QPaintEvent* event) override;
};

// Python menu_styles/modern.py 的 ModernCheckLayer：右侧蓝色实心圆 + 白勾。
class ModernCheckLayer : public QWidget {
    Q_OBJECT
public:
    explicit ModernCheckLayer(QMenu* parent);

protected:
    void paintEvent(QPaintEvent* event) override;
};

// Python menu_styles/common.py 的 ResponsiveMenuStyle。
class ResponsiveMenuStyle : public QProxyStyle {
public:
    using QProxyStyle::QProxyStyle;

    int styleHint(StyleHint hint, const QStyleOption* option = nullptr,
                  const QWidget* widget = nullptr, QStyleHintReturn* returnData = nullptr) const override;
};

// Python menu_styles/common.py 的 StayOpenMenuFilter：非 closeOnTrigger 叶子项
// 点击后触发命令但保持菜单打开。
class StayOpenMenuFilter : public QObject {
public:
    using QObject::QObject;

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;
};

// Python menu_styles/modern.py 的 apply_modern_menu_style
void applyModernMenuStyle(QMenu* menu, const MenuAppearance& appearance);

// Python menu_styles/common.py 的 inherit_menu_style（现代样式分支）
void inheritMenuStyle(QMenu* parent, QMenu* submenu);

// Python install_responsive_menu_style / install_stay_open_interaction / install_modern_check_indicators
void installResponsiveMenuStyle(QMenu* menu);
void installStayOpenFilter(QMenu* menu);
void installModernCheckIndicators(QMenu* menu);

// 读取菜单是否处于深色外观（Python menu.property("modernDark")）。
bool menuIsDark(const QMenu* menu);

} // namespace Pet::UI

Q_DECLARE_METATYPE(Pet::UI::MenuAppearance)
