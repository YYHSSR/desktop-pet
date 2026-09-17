#include "PetApplication.hpp"
#include "infrastructure/PathHelper.hpp"
#include "ui/PetFrameItem.hpp"
#include "ui/context_menus/MenuIconProvider.hpp"
#include <QQmlContext>
#include <QQuickWindow>
#include <QQuickImageProvider>
#include <QMessageBox>
#include <QTimer>
#include <QtQml>

namespace Pet::Application {

class PetImageProvider : public QQuickImageProvider {
public:
    explicit PetImageProvider(UI::PetWindowController* controller)
        : QQuickImageProvider(QQuickImageProvider::Image)
        , m_controller(controller) {}

    QImage requestImage(const QString& id, QSize* size, const QSize& requestedSize) override {
        Q_UNUSED(id);
        if (!m_controller) {
            return QImage();
        }
        QImage img = m_controller->currentImage();
        if (img.isNull()) {
            return QImage();
        }
        if (size) {
            *size = img.size();
        }
        if (requestedSize.isValid()) {
            return img.scaled(requestedSize, Qt::KeepAspectRatio, Qt::SmoothTransformation);
        }
        return img;
    }

private:
    UI::PetWindowController* m_controller = nullptr;
};

PetApplication::PetApplication(int& argc, char** argv)
    : m_app(std::make_unique<QApplication>(argc, argv))
    , m_engine(std::make_unique<QQmlApplicationEngine>()) {

    m_app->setApplicationName(QStringLiteral("desktop-pet"));
    m_app->setQuitOnLastWindowClosed(false);

    const QStringList arguments = m_app->arguments();
    m_config = std::make_unique<Infrastructure::ConfigManager>();
    m_controller = std::make_unique<UI::PetWindowController>(m_config.get());
    // 托盘直接驱动控制器（对齐 Python：托盘菜单与桌宠共用同一套行为入口）
    m_tray = std::make_unique<UI::SystemTrayBridge>(m_controller.get(), m_config.get());

    // 注册 QML 图像提供器 (提供 WebM 实时透明 RGBA 帧渲染)
    m_engine->addImageProvider("pet", new PetImageProvider(m_controller.get()));
    // 设置面板列表项图标（与右键菜单共用 MenuIcons）
    m_engine->addImageProvider("menuicon", new UI::MenuIconProvider());

    // 注册类型元数据
    qmlRegisterUncreatableType<UI::PetWindowController>("DesktopPet", 1, 0, "PetWindowController", "Cannot create in QML");
    qmlRegisterUncreatableType<Infrastructure::ConfigManager>("DesktopPet", 1, 0, "ConfigManager", "Cannot create in QML");
    qmlRegisterType<UI::PetFrameItem>("DesktopPet", 1, 0, "PetFrameItem");

    qDebug() << "Controller initialized:" << m_controller.get();

    // 暴露 C++ 控制器至 QML 环境
    m_engine->addImportPath(QCoreApplication::applicationDirPath() + "/qml");
    m_engine->addImportPath("qrc:/qt/qml");
    m_engine->rootContext()->setContextProperty("petController", m_controller.get());
    m_engine->rootContext()->setContextProperty("configManager", m_config.get());

    // 初始化系统托盘
    QString iconPath = Infrastructure::PathHelper::getAssetsDir() + "/icon.ico";
    m_tray->initTray(iconPath);

    connect(m_tray.get(), &UI::SystemTrayBridge::quitRequested, m_app.get(), &QApplication::quit);
    // Python win.on_hidden → _notify_pet_hidden：用户主动隐藏后提示恢复入口
    connect(m_controller.get(), &UI::PetWindowController::petHidden,
            m_tray.get(), &UI::SystemTrayBridge::notifyPetHidden);

    // 监听 QML 错误输出
    QObject::connect(m_engine.get(), &QQmlApplicationEngine::warnings, [](const QList<QQmlError>& errors) {
        for (const auto& err : errors) {
            qWarning() << "[QML Error]" << err.toString();
        }
    });

    // 加载 QML 主界面 (Qt 6 qt_add_qml_module 规范路径)
    const QUrl url(QStringLiteral("qrc:/qt/qml/DesktopPet/qml/MainPetWindow.qml"));
    QObject::connect(
        m_engine.get(),
        &QQmlApplicationEngine::objectCreated,
        m_app.get(),
        [url](QObject* obj, const QUrl& objUrl) {
            if (!obj && url == objUrl) {
                qCritical() << "Failed to instantiate QML root object from" << url;
                QCoreApplication::exit(-1);
            }
        },
        Qt::QueuedConnection
    );
    m_engine->load(url);

    if (!m_engine->rootObjects().isEmpty()) {
        QObject* root = m_engine->rootObjects().constFirst();
        connect(m_tray.get(), &UI::SystemTrayBridge::openSettingsRequested, root, [root]() {
            QMetaObject::invokeMethod(root, "openSettings", Qt::QueuedConnection);
        });
        connect(m_controller.get(), &UI::PetWindowController::openSettingsRequested, root, [root]() {
            QMetaObject::invokeMethod(root, "openSettings", Qt::QueuedConnection);
        });
        connect(m_controller.get(), &UI::PetWindowController::windowVisibleChanged, root, [root](bool visible) {
            root->setProperty("visible", visible);
        });
        // Useful for accessibility launchers and deterministic UI smoke tests.
        if (arguments.contains(QStringLiteral("--settings"))) {
            QMetaObject::invokeMethod(root, "openSettings", Qt::QueuedConnection);
        }
    }

    // Python app.py _check_autostart_wanted：启动 3500ms 后自检，
    // 配置里曾要求开机自启但注册表项被系统/安全软件移除时，弹气泡提醒。
    QTimer::singleShot(3500, this, [this]() {
        if (m_config && m_controller
            && m_config->value(QStringLiteral("autostart_wanted"), false).toBool()
            && !m_config->autostartEnabled()) {
            m_controller->showSpeech(
                QString::fromUtf8(u8"检测到开机自启已被系统或安全软件关闭，可在设置中重新启用。"),
                7000);
        }
    });
}

int PetApplication::exec() {
    if (m_startupExitCode != 0) {
        return m_startupExitCode;
    }
    return m_app->exec();
}

} // namespace Pet::Application
