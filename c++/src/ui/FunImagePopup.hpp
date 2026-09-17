#pragma once

#include <QPoint>
#include <QString>
#include <QStringList>
#include <QWidget>
#include <vector>

namespace Pet::UI {

// 对应 Python pet/ui/fun_image_popup.py：
// 无边框、可叠放、可拖动的彩蛋图片窗口。
class OjingjingWindow : public QWidget {
    Q_OBJECT
public:
    OjingjingWindow(const QString& imagePath, const QString& title, QWidget* parent = nullptr);

protected:
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void closeEvent(QCloseEvent* event) override;

private:
    bool m_dragging = false;
    QPoint m_dragStart;
    QPoint m_windowStart;
};

// 彩蛋窗口的弹窗路径解析（Python oijingjing_image_path / popup_image_paths / resolve_fun_asset）。
class FunAssets {
public:
    static QString oijingjingImagePath();
    static QStringList popupImagePaths(const QString& configuredDir);
};

// Python OjingjingWindowManager
class OjingjingWindowManager {
public:
    static OjingjingWindowManager& instance();

    void openWindow(const QString& configuredImageDir, const QString& title);
    void restoreAll();
    void closeAll();

    // 由 OjingjingWindow::closeEvent 回调（Python 的 _forget）。
    void forget(QWidget* window);

private:
    std::vector<QWidget*> m_windows;
};

// Python open_ojingjing_window(config)
void openOjingjingWindow(const QString& configuredImageDir, const QString& title);

} // namespace Pet::UI
