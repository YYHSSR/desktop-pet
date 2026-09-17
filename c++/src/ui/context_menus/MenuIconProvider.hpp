#pragma once

#include "MenuIcons.hpp"

#include <QIcon>
#include <QImage>
#include <QQuickImageProvider>
#include <QSize>
#include <QString>
#include <QUrl>

namespace Pet::UI {

// 设置面板「启动应用 / 快捷网址」列表项的图标提供器。
//
//   image://menuicon/app/<kind>/<percentEncodedPath>
//       真实程序图标（application）；default_browser 与取不到图标时回退矢量图标
//   image://menuicon/vector/<name>
//       内置矢量图标（web / add / settings / remove / retry 等）
//
// 与右键菜单共用 MenuIcons，保证设置界面与菜单里的图标完全一致。
class MenuIconProvider : public QQuickImageProvider {
public:
    MenuIconProvider()
        : QQuickImageProvider(QQuickImageProvider::Image) {}

    QImage requestImage(const QString& id, QSize* size, const QSize& requestedSize) override {
        const int extent = (requestedSize.isValid() && requestedSize.width() > 0)
            ? requestedSize.width()
            : 22;

        QIcon icon;
        if (id.startsWith(QStringLiteral("vector/"))) {
            icon = MenuIcons::vectorMenuIcon(nullptr, id.mid(7), extent);
        } else if (id.startsWith(QStringLiteral("app/"))) {
            const QString rest = id.mid(4);
            const qsizetype separator = rest.indexOf(QLatin1Char('/'));
            const QString kind = separator >= 0 ? rest.left(separator) : rest;
            const QString path = separator >= 0
                ? QUrl::fromPercentEncoding(rest.mid(separator + 1).toUtf8())
                : QString();
            icon = MenuIcons::quickAppIcon(nullptr, kind, path);
        }

        if (icon.isNull()) {
            return QImage();
        }
        const QImage image = icon.pixmap(extent, extent).toImage();
        if (size != nullptr) {
            *size = image.size();
        }
        return image;
    }
};

} // namespace Pet::UI
