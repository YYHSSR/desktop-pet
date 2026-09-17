#include "MenuExtras.hpp"

#include "MenuIcons.hpp"
#include "MenuStyle.hpp"
#include "ui/FunImagePopup.hpp"

#include "infrastructure/PathHelper.hpp"

#include <QCursor>
#include <QFontMetrics>
#include <QHBoxLayout>
#include <QLabel>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPixmap>
#include <QTimer>
#include <algorithm>

namespace Pet::UI {

namespace {

// Python fun_entry.py 的 _circle_photo
QPixmap circlePhoto(const QString& path, int size, qreal dpr) {
    QPixmap canvas(std::max(1, qRound(size * dpr)), std::max(1, qRound(size * dpr)));
    canvas.setDevicePixelRatio(dpr);
    canvas.fill(Qt::transparent);

    QPixmap source(path);
    if (source.isNull()) {
        source = QPixmap(FunAssets::oijingjingImagePath());
    }
    if (source.isNull()) {
        return canvas;
    }

    QPainter painter(&canvas);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
    QPainterPath clip;
    clip.addEllipse(QRectF(0.5, 0.5, size - 1.0, size - 1.0));
    painter.setClipPath(clip);
    const int crop = std::min(source.width(), source.height());
    const QRectF sourceRect((source.width() - crop) / 2.0, (source.height() - crop) / 2.0,
                            crop, crop);
    painter.drawPixmap(QRectF(0, 0, size, size), source, sourceRect);
    painter.end();
    return canvas;
}

// Python fun_entry.py 的 ElidedLabel
class ElidedLabel : public QLabel {
public:
    ElidedLabel(const QString& text, QWidget* parent)
        : QLabel(QString(), parent)
        , m_fullText(text) {
        setText(displayText());
    }

    QString displayText() const {
        return fontMetrics().elidedText(m_fullText, Qt::ElideRight, std::max(1, width()));
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        setText(displayText());
        QLabel::resizeEvent(event);
    }

    void changeEvent(QEvent* event) override {
        QLabel::changeEvent(event);
        if (event->type() == QEvent::FontChange) {
            setText(displayText());
        }
    }

private:
    QString m_fullText;
};

// Python fun_entry.py 的 ClickAccessory：指针图形 + 短提示文本。
class ClickAccessory : public QWidget {
public:
    ClickAccessory(QWidget* parent, const QString& text)
        : QWidget(parent) {
        setObjectName(QStringLiteral("ojingjingClickAccessory"));
        m_text = text.isEmpty() ? QString::fromUtf8(u8"请点击") : text.left(20);
        setProperty("text", m_text);
        setFixedSize(54, 20);

        QFont textFont = parent->font();
        const int pixelSize = textFont.pixelSize() > 0 ? textFont.pixelSize() : 13;
        textFont.setPixelSize(std::max(9, pixelSize - 2));
        setFont(textFont);
    }

    QString displayText() const {
        return QFontMetrics(font()).elidedText(m_text, Qt::ElideRight, 39);
    }

protected:
    void paintEvent(QPaintEvent* event) override {
        Q_UNUSED(event);
        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing, true);
        painter.setPen(QPen(QColor(QStringLiteral("#777777")), 1.25,
                            Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));

        QPainterPath pointer;
        pointer.moveTo(2.5, 3.0);
        pointer.lineTo(10.5, 10.0);
        pointer.lineTo(7.0, 10.7);
        pointer.lineTo(9.2, 15.2);
        pointer.lineTo(6.8, 16.4);
        pointer.lineTo(4.7, 11.9);
        pointer.lineTo(2.5, 14.5);
        pointer.closeSubpath();
        painter.drawPath(pointer);

        painter.setFont(font());
        painter.drawText(QRectF(15, 0, 39, 20), Qt::AlignVCenter, displayText());
    }

private:
    QString m_text;
};

// Python fun_entry.py 的 OjingjingMenuEntry
class OjingjingMenuEntry : public QWidget {
    Q_OBJECT
public:
    OjingjingMenuEntry(QMenu* menu, const QVariantMap& config, QWidget* parent = nullptr)
        : QWidget(parent)
        , m_menu(menu)
        , m_config(config) {
        setObjectName(QStringLiteral("ojingjingMenuEntry"));
        setCursor(Qt::PointingHandCursor);
        setMouseTracking(true);
        setMinimumWidth(224);
        setFixedHeight(39);

        auto* layout = new QHBoxLayout(this);
        layout->setContentsMargins(8, 2, 8, 2);
        layout->setSpacing(6);

        auto* avatar = new QLabel(this);
        avatar->setObjectName(QStringLiteral("ojingjingAvatar"));
        avatar->setFixedSize(27, 27);
        avatar->setAttribute(Qt::WA_TransparentForMouseEvents, true);
        const QString avatarPath = Infrastructure::PathHelper::resolveConfiguredAsset(
            m_config.value(QStringLiteral("avatar")).toString(), FunAssets::oijingjingImagePath());
        const qreal dpr = devicePixelRatioF() > 0.0 ? devicePixelRatioF() : 1.0;
        avatar->setPixmap(circlePhoto(avatarPath, 27, dpr));
        layout->addWidget(avatar);

        QString title = m_config.value(QStringLiteral("title")).toString();
        if (title.isEmpty()) {
            title = QString::fromUtf8(u8"厉害了我的鲸");
        }
        auto* titleLabel = new ElidedLabel(title, this);
        titleLabel->setObjectName(QStringLiteral("ojingjingTitle"));
        titleLabel->setFixedWidth(105);
        titleLabel->setAttribute(Qt::WA_TransparentForMouseEvents, true);
        titleLabel->setFont(menu->font());
        layout->addWidget(titleLabel);

        layout->addStretch(1);

        QString hint = m_config.value(QStringLiteral("hint")).toString();
        if (hint.isEmpty()) {
            hint = QString::fromUtf8(u8"请点击");
        }
        m_accessory = new ClickAccessory(this, hint);
        m_accessory->setAttribute(Qt::WA_TransparentForMouseEvents, true);
        layout->addWidget(m_accessory);
    }

    QSize sizeHint() const override { return QSize(224, 39); }
    QSize minimumSizeHint() const override { return QSize(224, 39); }

protected:
    void enterEvent(QEnterEvent* event) override {
        m_hovered = true;
        update();
        QWidget::enterEvent(event);
    }

    void leaveEvent(QEvent* event) override {
        // 鼠标移入子控件时也会触发 leave，光标仍在项内则保持高亮。
        m_hovered = rect().contains(mapFromGlobal(QCursor::pos()));
        update();
        QWidget::leaveEvent(event);
    }

    void showEvent(QShowEvent* event) override {
        QWidget::showEvent(event);
        // 菜单弹出时鼠标可能已在项上方，此时没有 enter 事件。
        m_hovered = rect().contains(mapFromGlobal(QCursor::pos()));
        update();
    }

    void mouseMoveEvent(QMouseEvent* event) override {
        m_hovered = true;
        update();
        QWidget::mouseMoveEvent(event);
    }

    void mouseReleaseEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton && rect().contains(event->position().toPoint())) {
            activate();
            event->accept();
            return;
        }
        QWidget::mouseReleaseEvent(event);
    }

    void paintEvent(QPaintEvent* event) override {
        Q_UNUSED(event);
        if (!m_hovered) {
            return;
        }
        QPainter painter(this);
        painter.setRenderHint(QPainter::Antialiasing, true);
        painter.setPen(Qt::NoPen);
        painter.setBrush(QColor(QStringLiteral("#eeeeee")));
        painter.drawRoundedRect(QRectF(0, 0, width(), height()), 9, 9);
    }

private:
    void activate() {
        const QVariantMap config = m_config;
        // 与 Python 一样等原生菜单跟踪循环结束后再创建顶层窗口。
        deferMenuCallback(m_menu, [config]() {
            openOjingjingWindow(config.value(QStringLiteral("image_dir")).toString(),
                                config.value(QStringLiteral("title")).toString());
        });
    }

    QMenu* m_menu = nullptr;
    QVariantMap m_config;
    ClickAccessory* m_accessory = nullptr;
    bool m_hovered = false;
};

} // namespace

QWidgetAction* addOjingjingEntry(QMenu* menu, const QVariantMap& config) {
    auto* action = new QWidgetAction(menu);
    QString title = config.value(QStringLiteral("title")).toString();
    if (title.isEmpty()) {
        title = QString::fromUtf8(u8"厉害了我的鲸");
    }
    action->setText(title);
    action->setDefaultWidget(new OjingjingMenuEntry(menu, config, nullptr));
    menu->addAction(action);
    return action;
}

QMenu* addAppLaunchMenu(QMenu* menu, const MenuContext& context) {
    const QVariantList apps = context.config != nullptr
        ? context.config->quickLaunchApps()
        : QVariantList();
    QMenu* submenu = addMenuSubmenu(menu, QString::fromUtf8(u8"启动应用"),
                                    QStringLiteral("application"));

    if (!apps.isEmpty()) {
        for (const QVariant& entry : apps) {
            const QVariantMap item = entry.toMap();
            const QString name = item.value(QStringLiteral("name")).toString();
            const QString path = item.value(QStringLiteral("path")).toString();
            const QString kind = item.value(QStringLiteral("kind")).toString();

            QAction* action = submenu->addAction(
                MenuIcons::quickAppIcon(submenu, kind, path),
                name.isEmpty() ? QString::fromUtf8(u8"应用") : name);
            action->setProperty("closeOnTrigger", true);
            connectAction(action, [context, path, kind]() {
                if (context.controller != nullptr) {
                    context.controller->launchQuickApp(path, kind);
                }
            });
        }
    } else {
        QAction* hint = submenu->addAction(
            MenuIcons::vectorMenuIcon(submenu, QStringLiteral("settings")),
            QString::fromUtf8(u8"去设置中添加应用..."));
        hint->setProperty("closeOnTrigger", true);
        connectAction(hint, [context]() {
            if (context.controller != nullptr) {
                context.controller->openModernSettings();
            }
        });
    }
    return submenu;
}

QMenu* addQuickWebsitesMenu(QMenu* menu, const MenuContext& context) {
    const QVariantList websites = context.config != nullptr
        ? context.config->quickWebsites()
        : QVariantList();
    QMenu* submenu = addMenuSubmenu(menu, QString::fromUtf8(u8"快捷网址"),
                                    QStringLiteral("web"));

    if (!websites.isEmpty()) {
        for (const QVariant& entry : websites) {
            const QVariantMap item = entry.toMap();
            const QString name = item.value(QStringLiteral("name")).toString().trimmed();
            const QString url = item.value(QStringLiteral("url")).toString().trimmed();
            const QString displayText = name.isEmpty() ? url : name;

            QAction* action = submenu->addAction(
                MenuIcons::vectorMenuIcon(submenu, QStringLiteral("web")), displayText);
            action->setToolTip(url);
            action->setProperty("closeOnTrigger", true);
            connectAction(action, [context, url]() {
                if (context.controller != nullptr) {
                    context.controller->openQuickWebsite(url);
                }
            });
        }
    } else {
        QAction* hint = submenu->addAction(
            MenuIcons::vectorMenuIcon(submenu, QStringLiteral("settings")),
            QString::fromUtf8(u8"去设置中添加网址..."));
        hint->setProperty("closeOnTrigger", true);
        connectAction(hint, [context]() {
            if (context.controller != nullptr) {
                context.controller->openModernSettings();
            }
        });
    }
    return submenu;
}

} // namespace Pet::UI

#include "MenuExtras.moc"
