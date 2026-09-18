// 设置面板结构与绑定校验：
// 直接加载 qml/ModernSettingsView.qml（真实 ConfigManager + 桩控制器），
// 断言 43 个组件化设置项与 15 张卡片的存在，并捕获任何 QML 警告。
#include "infrastructure/ConfigManager.hpp"
#include "ui/context_menus/MenuIconProvider.hpp"

#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQmlError>
#include <QTemporaryDir>

#include <cassert>
#include <iostream>

namespace {

// 仅提供设置面板需要的最小控制器接口（真实对话框不参与结构校验）。
class ControllerStub : public QObject {
    Q_OBJECT
public:
    Q_INVOKABLE void openClickTalkBindings() const {}
};

int countByType(QObject* object, const QString& needle) {
    int count = 0;
    if (object == nullptr) {
        return count;
    }
    const QString className = QString::fromLatin1(object->metaObject()->className());
    if (className.contains(needle + QStringLiteral("_QMLTYPE"))) {
        ++count;
    }
    const QObjectList children = object->children();
    for (QObject* child : children) {
        count += countByType(child, needle);
    }
    return count;
}

} // namespace

int main(int argc, char** argv) {
    QGuiApplication app(argc, argv);

    QTemporaryDir directory;
    assert(directory.isValid());
    Pet::Infrastructure::ConfigManager config(
        directory.filePath(QStringLiteral("config.json")));
    ControllerStub controller;

    QQmlApplicationEngine engine;
    QStringList warnings;
    QObject::connect(&engine, &QQmlApplicationEngine::warnings,
                     [&warnings](const QList<QQmlError>& errors) {
                         for (const QQmlError& error : errors) {
                             warnings.append(error.toString());
                         }
                     });

    engine.addImportPath(QStringLiteral(QT_QML_IMPORT_PATH));
    // 列表项图标由应用注册；这里注册同一实现，避免 "provider not found" 警告。
    engine.addImageProvider(QStringLiteral("menuicon"), new Pet::UI::MenuIconProvider());
    engine.rootContext()->setContextProperty(QStringLiteral("configManager"), &config);
    engine.rootContext()->setContextProperty(QStringLiteral("petController"), &controller);
    engine.load(QUrl::fromLocalFile(QStringLiteral(PET_QML_DIR "/ModernSettingsView.qml")));

    if (engine.rootObjects().isEmpty()) {
        std::cerr << "FAILED: ModernSettingsView.qml 未能实例化" << std::endl;
        for (const QString& warning : warnings) {
            std::cerr << "  " << warning.toStdString() << std::endl;
        }
        return 1;
    }
    QObject* root = engine.rootObjects().constFirst();

    // 结构断言：与 Python modern_settings_dialog.py 的设置项一一对应
    struct Expectation {
        const char* type;
        int count;
    };
    const Expectation expectations[] = {
        {"SettingsToggleRow", 14},
        {"SettingsChoiceRow", 7},
        {"SettingsSliderRow", 6},
        {"SettingsTextRow", 4},
        {"SettingsTextAreaRow", 1},
        {"SettingsPathRow", 3},
        {"SettingsColorRow", 6},
        {"SettingsButtonRow", 1},
        {"SettingsFontRow", 1},
        {"SettingsCard", 15},
    };

    int rowTotal = 0;
    bool failed = false;
    for (const Expectation& expectation : expectations) {
        const int actual = countByType(root, QString::fromLatin1(expectation.type));
        if (actual != expectation.count) {
            std::cerr << "FAILED: " << expectation.type << " 期望 " << expectation.count
                      << " 实际 " << actual << std::endl;
            failed = true;
        }
        if (QString::fromLatin1(expectation.type) != QStringLiteral("SettingsCard")) {
            rowTotal += actual;
        }
    }
    // 45 = 43 个组件化设置项 + 启动应用/快捷网址两个整块编辑器
    if (rowTotal != 43) {
        std::cerr << "FAILED: 组件化设置项总数 期望 43 实际 " << rowTotal << std::endl;
        failed = true;
    }

    if (!warnings.isEmpty()) {
        std::cerr << "FAILED: 加载过程中产生 " << warnings.size() << " 条 QML 警告" << std::endl;
        for (const QString& warning : warnings) {
            std::cerr << "  " << warning.toStdString() << std::endl;
        }
        failed = true;
    }

    if (failed) {
        return 1;
    }
    std::cout << "Settings view structure OK: " << rowTotal
              << " component rows + 2 editor blocks = " << (rowTotal + 2) << " settings."
              << std::endl;
    return 0;
}

#include "test_settings_view.moc"
