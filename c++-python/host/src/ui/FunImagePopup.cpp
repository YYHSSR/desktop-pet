#include "FunImagePopup.hpp"

#include "infrastructure/PathHelper.hpp"

#include <QApplication>
#include <QCloseEvent>
#include <QDir>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QImageReader>
#include <QLabel>
#include <QMouseEvent>
#include <QPixmap>
#include <QPushButton>
#include <QRandomGenerator>
#include <QScreen>
#include <QVBoxLayout>
#include <algorithm>

namespace Pet::UI {

namespace {

const char* kWindowStyleSheet =
    "#ojingjingCard { background: white; border: 1px solid #d8d8d8; border-radius: 16px; }"
    "QPushButton { min-height: 30px; padding: 0 16px; border: 1px solid #d5d5d5; "
    "border-radius: 8px; background: #f7f7f7; color: #222; font-size: 13px; }"
    "QPushButton:hover { background: #ececec; }"
    "QPushButton#closeAllButton { background: #202020; color: white; border-color: #202020; }";

QStringList readableImagePaths(const QString& directory) {
    QDir dir(directory);
    if (!dir.exists()) {
        return {};
    }
    static const QStringList kSupported = {
        QStringLiteral("*.jpg"), QStringLiteral("*.jpeg"), QStringLiteral("*.png"),
        QStringLiteral("*.webp"), QStringLiteral("*.bmp"), QStringLiteral("*.gif"),
        QStringLiteral("*.tif"), QStringLiteral("*.tiff"),
    };
    QStringList result;
    const QFileInfoList entries = dir.entryInfoList(kSupported, QDir::Files, QDir::Name);
    for (const QFileInfo& info : entries) {
        if (QImageReader(info.absoluteFilePath()).canRead()) {
            result.append(info.absoluteFilePath());
        }
    }
    return result;
}

} // namespace

// ---------------------------------------------------------------- FunAssets

QString FunAssets::oijingjingImagePath() {
    return QDir(Infrastructure::PathHelper::getAssetsDir())
        .filePath(QStringLiteral("big_blue_fat_fish/ojingjing.jpg"));
}

QStringList FunAssets::popupImagePaths(const QString& configuredDir) {
    const QString fallbackDir = QFileInfo(oijingjingImagePath()).absolutePath();
    const QString requested = Infrastructure::PathHelper::resolveConfiguredAsset(
        configuredDir, fallbackDir);
    const QStringList paths = readableImagePaths(requested);
    if (!paths.isEmpty() || QDir::cleanPath(requested) == QDir::cleanPath(fallbackDir)) {
        return paths;
    }
    return readableImagePaths(fallbackDir);
}

// ---------------------------------------------------------------- OjingjingWindow

OjingjingWindow::OjingjingWindow(const QString& imagePath, const QString& title, QWidget* parent)
    : QWidget(parent) {
    setObjectName(QStringLiteral("ojingjingImageWindow"));
    setProperty("sourceImage", imagePath);
    setWindowTitle(title.isEmpty() ? QString::fromUtf8(u8"厉害了我的鲸") : title);
    setWindowFlags(Qt::Window | Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint);
    setAttribute(Qt::WA_DeleteOnClose, true);
    setAttribute(Qt::WA_TranslucentBackground, true);

    QScreen* screen = QApplication::primaryScreen();
    const QRect available = screen != nullptr ? screen->availableGeometry() : QRect();
    const int side = available.isNull()
        ? 520
        : std::max(280, std::min(560, static_cast<int>(available.height() * 0.64)));

    auto* card = new QWidget(this);
    card->setObjectName(QStringLiteral("ojingjingCard"));
    card->setStyleSheet(QString::fromLatin1(kWindowStyleSheet));

    auto* cardLayout = new QVBoxLayout(card);
    cardLayout->setContentsMargins(10, 10, 10, 10);
    cardLayout->setSpacing(10);

    auto* image = new QLabel(card);
    image->setObjectName(QStringLiteral("ojingjingFullImage"));
    image->setAlignment(Qt::AlignCenter);
    const QPixmap source(imagePath);
    if (!source.isNull()) {
        image->setPixmap(source.scaled(side, side, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    }
    image->setFixedSize(side, side);
    image->setAttribute(Qt::WA_TransparentForMouseEvents, true);
    cardLayout->addWidget(image);

    auto* controls = new QHBoxLayout();
    controls->addStretch(1);
    auto* closeButton = new QPushButton(QString::fromUtf8(u8"关闭"), card);
    closeButton->setObjectName(QStringLiteral("closeButton"));
    QObject::connect(closeButton, &QPushButton::clicked, this, &QWidget::close);
    controls->addWidget(closeButton);

    auto* closeAllButton = new QPushButton(QString::fromUtf8(u8"全部关闭"), card);
    closeAllButton->setObjectName(QStringLiteral("closeAllButton"));
    QObject::connect(closeAllButton, &QPushButton::clicked, this, []() {
        OjingjingWindowManager::instance().closeAll();
    });
    controls->addWidget(closeAllButton);
    cardLayout->addLayout(controls);

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(8, 8, 8, 8);
    root->addWidget(card);
    // 内容尺寸是确定的，避免在无边框半透明窗口上调用 adjustSize()。
    setFixedSize(side + 36, side + 82);
}

void OjingjingWindow::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        m_dragging = true;
        m_dragStart = event->globalPosition().toPoint();
        m_windowStart = pos();
        event->accept();
        return;
    }
    QWidget::mousePressEvent(event);
}

void OjingjingWindow::mouseMoveEvent(QMouseEvent* event) {
    if (m_dragging && (event->buttons() & Qt::LeftButton)) {
        move(m_windowStart + event->globalPosition().toPoint() - m_dragStart);
        event->accept();
        return;
    }
    QWidget::mouseMoveEvent(event);
}

void OjingjingWindow::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton && m_dragging) {
        move(m_windowStart + event->globalPosition().toPoint() - m_dragStart);
        m_dragging = false;
        event->accept();
        return;
    }
    QWidget::mouseReleaseEvent(event);
}

void OjingjingWindow::closeEvent(QCloseEvent* event) {
    OjingjingWindowManager::instance().forget(this);
    QWidget::closeEvent(event);
}

// ---------------------------------------------------------------- OjingjingWindowManager

OjingjingWindowManager& OjingjingWindowManager::instance() {
    static OjingjingWindowManager manager;
    return manager;
}

void OjingjingWindowManager::forget(QWidget* window) {
    m_windows.erase(std::remove(m_windows.begin(), m_windows.end(), window), m_windows.end());
}

void OjingjingWindowManager::openWindow(const QString& configuredImageDir, const QString& title) {
    const QStringList paths = FunAssets::popupImagePaths(configuredImageDir);
    if (paths.isEmpty()) {
        qWarning("彩蛋图片目录中没有可读取的图片: %s",
                 qUtf8Printable(FunAssets::oijingjingImagePath()));
        return;
    }

    restoreAll();
    const int index = QRandomGenerator::global()->bounded(paths.size());
    auto* window = new OjingjingWindow(
        paths.at(index),
        title.isEmpty() ? QString::fromUtf8(u8"厉害了我的鲸") : title);

    const int offset = static_cast<int>(m_windows.size() % 7) * 24;
    window->setProperty("cascadeOffset", offset);
    if (QScreen* screen = QApplication::primaryScreen()) {
        const QRect available = screen->availableGeometry();
        window->move(available.center().x() - window->width() / 2 + offset,
                     available.center().y() - window->height() / 2 + offset);
    }
    m_windows.push_back(window);
    window->show();
    window->raise();
    window->activateWindow();
}

void OjingjingWindowManager::restoreAll() {
    for (QWidget* window : m_windows) {
        if (window == nullptr) {
            continue;
        }
        window->show();
        window->raise();
    }
    if (!m_windows.empty() && m_windows.back() != nullptr) {
        m_windows.back()->activateWindow();
    }
}

void OjingjingWindowManager::closeAll() {
    const std::vector<QWidget*> windows = m_windows;
    m_windows.clear();
    for (QWidget* window : windows) {
        if (window != nullptr) {
            window->close();
        }
    }
}

void openOjingjingWindow(const QString& configuredImageDir, const QString& title) {
    OjingjingWindowManager::instance().openWindow(configuredImageDir, title);
}

} // namespace Pet::UI
