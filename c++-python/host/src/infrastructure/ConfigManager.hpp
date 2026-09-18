#pragma once

#include <QObject>
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVariant>
#include <QVariantList>
#include <QVariantMap>
#include <QUrl>
#include <limits>

namespace Pet::Infrastructure {

// Shares the Python application's JSON contract. Unknown fields are retained,
// so either executable may update its own settings without erasing the rest.
class ConfigManager : public QObject {
    Q_OBJECT
    Q_PROPERTY(double scale READ scale WRITE setScale NOTIFY scaleChanged)
    Q_PROPERTY(QString character READ character WRITE setCharacter NOTIFY characterChanged)
    Q_PROPERTY(bool autoHideFullscreen READ autoHideFullscreen WRITE setAutoHideFullscreen NOTIFY autoHideFullscreenChanged)
    Q_PROPERTY(bool lockPosition READ lockPosition WRITE setLockPosition NOTIFY lockPositionChanged)
    Q_PROPERTY(int petOpacity READ petOpacity WRITE setPetOpacity NOTIFY petOpacityChanged)
    Q_PROPERTY(double playbackSpeed READ playbackSpeed WRITE setPlaybackSpeed NOTIFY playbackSpeedChanged)
    Q_PROPERTY(bool onTop READ onTop WRITE setOnTop NOTIFY onTopChanged)
    Q_PROPERTY(QVariantList quickLaunchApps READ quickLaunchApps WRITE setQuickLaunchApps NOTIFY quickLaunchAppsChanged)
    Q_PROPERTY(QVariantList quickWebsites READ quickWebsites WRITE setQuickWebsites NOTIFY quickWebsitesChanged)

public:
    explicit ConfigManager(const QString& filePathOverride = {}, QObject* parent = nullptr);

    bool load();
    Q_INVOKABLE bool save();
    QString filePath() const { return m_filePath; }
    QJsonObject rawObject() const { return m_document; }

    double scale() const noexcept { return m_scale; }
    void setScale(double value);
    QString character() const { return m_character; }
    void setCharacter(const QString& value);
    bool autoHideFullscreen() const noexcept { return m_autoHideFullscreen; }
    void setAutoHideFullscreen(bool value);
    bool lockPosition() const noexcept { return m_lockPosition; }
    void setLockPosition(bool value);
    int petOpacity() const noexcept { return m_petOpacity; }
    void setPetOpacity(int value);
    double playbackSpeed() const noexcept { return m_playbackSpeed; }
    void setPlaybackSpeed(double value);
    bool onTop() const noexcept { return m_onTop; }
    void setOnTop(bool value);
    QVariantList quickLaunchApps() const;
    void setQuickLaunchApps(const QVariantList& value);
    QVariantList quickWebsites() const;
    void setQuickWebsites(const QVariantList& value);

    Q_INVOKABLE QVariant value(const QString& key, const QVariant& fallback = {}) const;
    Q_INVOKABLE void setValue(const QString& key, const QVariant& value);
    Q_INVOKABLE QString localPath(const QUrl& url) const { return url.toLocalFile(); }
    Q_INVOKABLE bool autostartEnabled() const;
    Q_INVOKABLE bool setAutostartEnabled(bool enabled);

    // ---- 右键菜单所需的只读视图（默认值/清洗规则与 Python config.py 一致）----
    // 外观：theme/density/corner_radius/ui_font/ui_font_size/translucent/opacity/light_*/dark_*
    QVariantMap menuAppearanceValues() const;
    // 彩蛋首行：enabled/title/hint/avatar/image_dir
    QVariantMap menuEasterEggValues() const;
    // Agent 联动：内置三项 + custom_agents + 气泡提醒 + 思考文案，
    // 默认值/清洗规则对齐 Python _clean_agent_link_data。
    QVariantMap agentLinkValues() const;
    // 写入 agent_link 下的单个键（Python set_enabled / _set_agent_link_option 等价物）。
    Q_INVOKABLE void setAgentLinkValue(const QString& key, const QVariant& value);
    // ---- 混合架构（C++ 宿主 + Python Worker）----
    // python_worker：enabled / python_path / worker_entry，默认值与清洗规则在此收敛。
    // Worker 是唯一的 Agent 业务实现，无独立后端开关。
    QVariantMap pythonWorkerValues() const;
    Q_INVOKABLE void setPythonWorkerValue(const QString& key, const QVariant& value);
    // ---- 设置面板所需读写（对齐 Python config.py / modern_settings_dialog.py）----
    // 系统字体族列表（Python _system_font_families，结果缓存）
    Q_INVOKABLE QStringList systemFontFamilies();
    // 内置彩蛋素材默认路径（Python oijingjing_image_path 及其 parent）
    Q_INVOKABLE QString defaultEasterEggAvatar() const;
    Q_INVOKABLE QString defaultEasterEggImageDir() const;
    // 素材路径解析（Python resolve_fun_asset）：空值/不存在回退默认
    Q_INVOKABLE QString resolveAssetPath(const QString& configured, const QString& fallback) const;
    // 素材路径持久化（Python store_fun_asset）：内置 assets 内归一化为相对值
    Q_INVOKABLE QString storeAssetPath(const QString& value, const QString& fallback) const;
    // 自言自语默认候选文本（Python DEFAULT_SELF_TALK_TEXTS）
    Q_INVOKABLE QVariantList defaultSelfTalkTexts() const;
    // Agent 思考文案 {agentKey: text}（Python agent_link.thinking_texts，兼容旧 thinking_text）
    Q_INVOKABLE QVariantMap agentThinkingTexts() const;
    Q_INVOKABLE void setAgentThinkingTexts(const QVariantMap& texts);
    Q_INVOKABLE QString agentThinkingText(const QString& agentKey) const;
    Q_INVOKABLE void setAgentThinkingText(const QString& agentKey, const QString& text);
    // 点击动画台词绑定（Python character_profiles.<角色>.click_talk_bindings）
    Q_INVOKABLE QVariantMap clickTalkBindings(const QString& characterId) const;
    Q_INVOKABLE void setClickTalkBindings(const QString& characterId,
                                          const QVariantMap& bindings);

    // 角色显示名别名；未设置返回空串
    QString characterAlias(const QString& characterId) const;
    // 设置角色显示名别名（最长 24 字符，空名恢复默认），与 Python 行为一致。
    void setCharacterAlias(const QString& characterId, const QString& name);
    // no_move 当前值（Python pet.no_move）
    bool noMove() const;

    bool hasStoredPosition() const noexcept;
    double storedX() const noexcept { return m_rx; }
    double storedY() const noexcept { return m_ry; }
    QString facing() const { return m_facing; }
    void setWindowState(double x, double y, const QString& facing);

signals:
    void scaleChanged(double value);
    void characterChanged(const QString& value);
    void autoHideFullscreenChanged(bool value);
    void lockPositionChanged(bool value);
    void petOpacityChanged(int value);
    void playbackSpeedChanged(double value);
    void onTopChanged(bool value);
    void quickLaunchAppsChanged();
    void quickWebsitesChanged();
    void valueChanged(const QString& key, const QVariant& value);

private:
    void applyDocument();
    void updateDocument();

    QString m_filePath;
    QJsonObject m_document;
    double m_scale = 0.72;
    QString m_character = QStringLiteral("shenshen");
    bool m_autoHideFullscreen = true;
    bool m_lockPosition = false;
    int m_petOpacity = 100;
    double m_playbackSpeed = 1.0;
    bool m_onTop = true;
    double m_rx = std::numeric_limits<double>::quiet_NaN();
    double m_ry = std::numeric_limits<double>::quiet_NaN();
    QString m_facing = QStringLiteral("left");
};

} // namespace Pet::Infrastructure
