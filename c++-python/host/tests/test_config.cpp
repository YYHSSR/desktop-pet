#include "infrastructure/ConfigManager.hpp"
#include "infrastructure/PathHelper.hpp"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>
#include <QTemporaryDir>
#include <cassert>
#include <cmath>
#include <iostream>

using Pet::Infrastructure::ConfigManager;

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    QTemporaryDir directory;
    assert(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("config.json"));

    QJsonObject source;
    source[QStringLiteral("version")] = 4;
    source[QStringLiteral("scale")] = 0.85;
    source[QStringLiteral("character")] = QStringLiteral("shenshen");
    source[QStringLiteral("unknown_python_setting")] = QJsonObject{{QStringLiteral("keep"), true}};
    QFile initial(path);
    assert(initial.open(QIODevice::WriteOnly));
    initial.write(QJsonDocument(source).toJson());
    initial.close();

    ConfigManager config(path);
    assert(std::abs(config.scale() - 0.85) < 1e-9);

    config.setScale(1.2);
    QFile saved(path);
    assert(saved.open(QIODevice::ReadOnly));
    const QJsonObject result = QJsonDocument::fromJson(saved.readAll()).object();
    assert(std::abs(result.value(QStringLiteral("scale")).toDouble() - 1.2) < 1e-9);
    assert(result.value(QStringLiteral("unknown_python_setting")).toObject()
        .value(QStringLiteral("keep")).toBool());

    QVariantMap quickApp;
    quickApp[QStringLiteral("name")] = QStringLiteral("Tool");
    quickApp[QStringLiteral("path")] = QStringLiteral("C:/Tool.exe");
    quickApp[QStringLiteral("kind")] = QStringLiteral("application");
    config.setQuickLaunchApps(QVariantList{quickApp});

    QFile savedOnce(path);
    assert(savedOnce.open(QIODevice::ReadOnly));
    const QJsonObject savedObject = QJsonDocument::fromJson(savedOnce.readAll()).object();
    assert(savedObject.value(QStringLiteral("quick_launch_apps")).isArray());
    assert(savedObject.value(QStringLiteral("quick_launch_apps")).toArray().size() == 1);

    ConfigManager restarted(path);
    assert(restarted.quickLaunchApps().size() == 1);

    // ---- 设置面板支撑接口（阶段二）----

    // 自言自语默认候选文本数量（Python DEFAULT_SELF_TALK_TEXTS）
    assert(restarted.defaultSelfTalkTexts().size() == 22);

    // Agent 思考文案：写入 / 读回 / 留空移除
    restarted.setAgentThinkingText(QStringLiteral("cursor"), QStringLiteral("Cursor 构思中…"));
    assert(restarted.agentThinkingText(QStringLiteral("cursor"))
           == QStringLiteral("Cursor 构思中…"));
    restarted.setAgentThinkingText(QStringLiteral("cursor"), QString());
    assert(restarted.agentThinkingText(QStringLiteral("cursor")).isEmpty());
    // 旧全局 thinking_text 字段作为回退来源（Python 迁移兼容）
    restarted.setValue(QStringLiteral("agent_link.thinking_text"), QStringLiteral("旧的全局文案"));
    assert(restarted.agentThinkingText(QStringLiteral("chatgpt"))
           == QStringLiteral("旧的全局文案"));
    restarted.setAgentThinkingTexts(
        QVariantMap{{QStringLiteral("chatgpt"), QStringLiteral("新的按 Agent 文案")}});
    assert(restarted.agentThinkingText(QStringLiteral("chatgpt"))
           == QStringLiteral("新的按 Agent 文案"));
    // 保存后旧字段被清理，未设置的 Agent 不再回退到旧文案
    assert(restarted.agentThinkingText(QStringLiteral("antigravity")).isEmpty());

    // 点击动画台词绑定（Python character_profiles.<角色>.click_talk_bindings）
    restarted.setClickTalkBindings(
        QStringLiteral("shenshen"),
        QVariantMap{{QStringLiteral("click_a"), QStringList{QStringLiteral("台词一"),
                                                            QStringLiteral("台词二")}},
                    {QStringLiteral("click_empty"), QStringList{}}});
    const QVariantMap bindings = restarted.clickTalkBindings(QStringLiteral("shenshen"));
    assert(bindings.contains(QStringLiteral("click_a")));
    assert(!bindings.contains(QStringLiteral("click_empty"))); // 空绑定即移除
    assert(bindings.value(QStringLiteral("click_a")).toStringList().size() == 2);

    // Agent 联动清洗：补齐默认值与自定义 Agent 列表
    const QVariantMap agentValues = restarted.agentLinkValues();
    assert(agentValues.value(QStringLiteral("notify_done")).toBool());
    assert(!agentValues.value(QStringLiteral("notify_activity")).toBool());

    // 素材路径归一化：内置 assets 内转相对值，外部路径保留绝对形式
    const QString assetsDir = Pet::Infrastructure::PathHelper::getAssetsDir();
    const QString insideAssets = QDir(assetsDir).filePath(QStringLiteral("big_blue_fat_fish/ojingjing.jpg"));
    assert(restarted.storeAssetPath(insideAssets, QString())
           == QStringLiteral("assets/big_blue_fat_fish/ojingjing.jpg"));
    assert(restarted.storeAssetPath(QStringLiteral("D:/pics/a.png"), QString())
           == QStringLiteral("D:/pics/a.png"));
    // 空值回退默认（Python store_fun_asset）
    assert(restarted.storeAssetPath(QString(), insideAssets).contains(QStringLiteral("ojingjing.jpg")));

    std::cout << "Config compatibility tests passed." << std::endl;
    return 0;
}
