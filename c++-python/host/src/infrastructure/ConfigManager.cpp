#include "ConfigManager.hpp"
#include "PathHelper.hpp"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFontDatabase>
#include <QJsonDocument>
#include <QJsonArray>
#include <QSaveFile>
#include <QCoreApplication>
#include <QRegularExpression>
#include <QSet>
#include <QSettings>
#include <algorithm>
#include <cmath>

namespace Pet::Infrastructure {
namespace {

QJsonArray defaultQuickApps() {
    return QJsonArray{QJsonObject{
        {QStringLiteral("name"), QStringLiteral("默认浏览器")},
        {QStringLiteral("path"), QString()},
        {QStringLiteral("kind"), QStringLiteral("default_browser")}
    }};
}

QJsonArray defaultQuickWebsites() {
    return QJsonArray{QJsonObject{
        {QStringLiteral("name"), QStringLiteral("GitHub 项目页")},
        {QStringLiteral("url"), QStringLiteral("https://github.com/MerZlin/dsh-pet-indesktop")}
    }};
}

double finiteNumber(const QJsonObject& object, const char* key, double fallback,
                    double minimum, double maximum) {
    const QJsonValue value = object.value(QLatin1String(key));
    if (!value.isDouble() || !std::isfinite(value.toDouble())) {
        return fallback;
    }
    return std::clamp(value.toDouble(), minimum, maximum);
}

bool boolValue(const QJsonObject& object, const char* key, bool fallback) {
    const QJsonValue value = object.value(QLatin1String(key));
    return value.isBool() ? value.toBool() : fallback;
}

// Python bool(value)：JSON 假值集合为 null / false / 0 / "" / [] / {}
bool jsonTruthy(const QJsonValue& value) {
    switch (value.type()) {
    case QJsonValue::Bool:
        return value.toBool();
    case QJsonValue::Double:
        return value.toDouble() != 0.0;
    case QJsonValue::String:
        return !value.toString().isEmpty();
    case QJsonValue::Array:
        return !value.toArray().isEmpty();
    case QJsonValue::Object:
        return !value.toObject().isEmpty();
    case QJsonValue::Null:
    case QJsonValue::Undefined:
        break;
    }
    return false;
}

// Python _float_or_default(value, default, lo, hi)
double floatOrDefault(const QJsonValue& value, double fallback, double minimum, double maximum) {
    if (!value.isDouble()) {
        return fallback;
    }
    const double number = value.toDouble();
    if (!std::isfinite(number)) {
        return fallback;
    }
    return std::clamp(number, minimum, maximum);
}

// Python config._clean_custom_agents（上限 8 条，key 不得与内置键或已移除键重复）
QJsonArray cleanCustomAgents(const QJsonValue& raw) {
    static const QSet<QString> reserved = {
        // 内置键
        QStringLiteral("antigravity"), QStringLiteral("chatgpt"),
        // 已移除的历史键：拒绝旧配置把它们复活成自定义 Agent
        QStringLiteral("dsh"), QStringLiteral("claude"), QStringLiteral("deepseek"),
        QStringLiteral("cursor"),
    };
    static const QRegularExpression keyPattern(QStringLiteral("^[a-z0-9][a-z0-9_-]{0,31}$"));

    QJsonArray result;
    if (!raw.isArray()) {
        return result;
    }
    QSet<QString> seen;
    const QJsonArray items = raw.toArray();
    for (const QJsonValue& item : items) {
        if (result.size() >= 8) {
            break;
        }
        if (!item.isObject()) {
            continue;
        }
        const QJsonObject object = item.toObject();
        const QString key = object.value(QStringLiteral("key")).toString().trimmed().toLower();
        if (!keyPattern.match(key).hasMatch() || reserved.contains(key) || seen.contains(key)) {
            continue;
        }
        const QString path = object.value(QStringLiteral("path")).toString().trimmed().left(500);
        if (path.isEmpty()) {
            continue;
        }
        const QString name = object.value(QStringLiteral("name")).toString().trimmed().left(50);
        seen.insert(key);
        result.append(QJsonObject{
            {QStringLiteral("key"), key},
            {QStringLiteral("name"), name.isEmpty() ? key : name},
            {QStringLiteral("path"), path},
        });
    }
    return result;
}

// 已删除功能遗留在配置里的顶层键：读取时清理并落盘，
// 避免旧版本升级后无用字段被一直保留（C++ 侧默认保留未知字段）。
bool pruneRemovedFeatureKeys(QJsonObject* document) {
    static const char* const keys[] = {
        "proactive_screen",           // 主动识屏已整体移除
        "collision_enabled",          // 多开碰撞已整体移除
        "collision_restitution",
        "collision_friction",
        "collision_mass_scale",
        "collision_impulse_cap",
        "collision_sound_enabled",
        "collision_sound_volume",
        "click_sound_path",           // 音效功能已整体移除
        "click_sound_pack",
        "click_sound_enabled",
        "click_sound_volume",
        "context_menu_template",      // 菜单模板只剩 modern
        "soundEnabled",
        "soundVolume",
        "drag_physics",               // 拖动物理已整体移除（连带弹弓/甩出）
        "slingshot_enabled",
        "throw_strength",
        "throw_max_speed",
        "mouse_through",              // 鼠标穿透已整体移除
        "cursor_hidden_passthrough",
    };
    bool changed = false;
    for (const char* key : keys) {
        const QString name = QLatin1String(key);
        if (document->contains(name)) {
            document->remove(name);
            changed = true;
        }
    }

    // agent_link 下的音效子键随音效功能一并清理
    const QString agentLinkKey = QStringLiteral("agent_link");
    if (document->contains(agentLinkKey)) {
        QJsonObject agentLink = document->value(agentLinkKey).toObject();
        static const char* const agentSoundKeys[] = {
            "sound_enabled", "sound_start_path", "sound_done_path", "sound_error_path",
            "sound_volume", "sound_cooldown_seconds",
            "sound_start_enabled", "sound_done_enabled", "sound_error_enabled",
        };
        for (const char* key : agentSoundKeys) {
            const QString name = QLatin1String(key);
            if (agentLink.contains(name)) {
                agentLink.remove(name);
                changed = true;
            }
        }
        (*document)[agentLinkKey] = agentLink;
    }
    return changed;
}

} // namespace

ConfigManager::ConfigManager(const QString& filePathOverride, QObject* parent)
    : QObject(parent)
    , m_filePath(filePathOverride.isEmpty() ? PathHelper::getConfigPath() : filePathOverride) {
    load();
}

bool ConfigManager::load() {
    QFile file(m_filePath);
    if (!file.exists()) {
        m_document = QJsonObject{{QStringLiteral("version"), 4}};
        return true;
    }
    if (!file.open(QIODevice::ReadOnly)) {
        return false;
    }
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        // 对齐 Python config.py：解析失败先备份现场再以默认配置继续，
        // 绝不让一个坏文件卡死启动（用户可从备份里找回旧设置）
        file.close();
        const QString backup = m_filePath + QStringLiteral(".corrupt-")
            + QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss"));
        file.rename(backup);
        m_document = QJsonObject{{QStringLiteral("version"), 4}};
        qWarning() << "[Config] 配置解析失败，原文件已备份到:" << backup;
        return false;
    }
    m_document = document.object();
    const bool pruned = pruneRemovedFeatureKeys(&m_document);
    applyDocument();
    if (pruned) {
        save(); // 清掉的键立即落盘，避免下次读取时又被带回来
    }
    return true;
}

void ConfigManager::applyDocument() {
    m_scale = finiteNumber(m_document, "scale", 0.72, 0.1, 8.0);
    m_autoHideFullscreen = boolValue(m_document, "auto_hide_fullscreen", true);
    m_lockPosition = boolValue(m_document, "lock_position", false);
    m_petOpacity = static_cast<int>(finiteNumber(m_document, "pet_opacity", 100.0, 10.0, 100.0));
    m_playbackSpeed = finiteNumber(m_document, "playback_speed", 1.0, 0.1, 8.0);
    m_onTop = boolValue(m_document, "on_top", true);

    const QString character = m_document.value(QStringLiteral("character")).toString().trimmed();
    m_character = character.isEmpty() ? QStringLiteral("shenshen") : character;
    const QString facingValue = m_document.value(QStringLiteral("facing")).toString();
    m_facing = facingValue == QStringLiteral("right") ? facingValue : QStringLiteral("left");
    m_rx = m_document.value(QStringLiteral("rx")).isDouble()
        ? m_document.value(QStringLiteral("rx")).toDouble() : std::numeric_limits<double>::quiet_NaN();
    m_ry = m_document.value(QStringLiteral("ry")).isDouble()
        ? m_document.value(QStringLiteral("ry")).toDouble() : std::numeric_limits<double>::quiet_NaN();
}

void ConfigManager::updateDocument() {
    m_document[QStringLiteral("version")] = 4;
    m_document[QStringLiteral("scale")] = m_scale;
    m_document[QStringLiteral("character")] = m_character;
    m_document[QStringLiteral("auto_hide_fullscreen")] = m_autoHideFullscreen;
    m_document[QStringLiteral("lock_position")] = m_lockPosition;
    m_document[QStringLiteral("pet_opacity")] = m_petOpacity;
    m_document[QStringLiteral("playback_speed")] = m_playbackSpeed;
    m_document[QStringLiteral("on_top")] = m_onTop;
    m_document[QStringLiteral("facing")] = m_facing;
    if (hasStoredPosition()) {
        m_document[QStringLiteral("rx")] = m_rx;
        m_document[QStringLiteral("ry")] = m_ry;
    }
}

bool ConfigManager::save() {
    updateDocument();
    QDir().mkpath(QFileInfo(m_filePath).absolutePath());
    QSaveFile file(m_filePath);
    if (!file.open(QIODevice::WriteOnly)) {
        return false;
    }
    const QByteArray bytes = QJsonDocument(m_document).toJson(QJsonDocument::Indented);
    return file.write(bytes) == bytes.size() && file.commit();
}

#define UPDATE_VALUE(member, value, signalName) \
    do { if ((member) != (value)) { (member) = (value); emit signalName(member); save(); } } while (false)

void ConfigManager::setScale(double value) {
    value = std::clamp(value, 0.1, 8.0);
    if (std::abs(m_scale - value) > 1e-4) { m_scale = value; emit scaleChanged(value); save(); }
}
void ConfigManager::setAutoHideFullscreen(bool value) { UPDATE_VALUE(m_autoHideFullscreen, value, autoHideFullscreenChanged); }
void ConfigManager::setLockPosition(bool value) { UPDATE_VALUE(m_lockPosition, value, lockPositionChanged); }
void ConfigManager::setPetOpacity(int value) {
    value = std::clamp(value, 10, 100);
    UPDATE_VALUE(m_petOpacity, value, petOpacityChanged);
}
void ConfigManager::setPlaybackSpeed(double value) {
    value = std::clamp(value, 0.1, 8.0);
    if (std::abs(m_playbackSpeed - value) > 1e-4) { m_playbackSpeed = value; emit playbackSpeedChanged(value); save(); }
}
void ConfigManager::setOnTop(bool value) { UPDATE_VALUE(m_onTop, value, onTopChanged); }

#undef UPDATE_VALUE

void ConfigManager::setCharacter(const QString& value) {
    const QString clean = value.trimmed();
    if (!clean.isEmpty() && clean != m_character) {
        m_character = clean;
        emit characterChanged(clean);
        save();
    }
}

QVariantList ConfigManager::quickLaunchApps() const {
    const QJsonValue stored = m_document.value(QStringLiteral("quick_launch_apps"));
    return (stored.isArray() ? stored.toArray() : defaultQuickApps()).toVariantList();
}

void ConfigManager::setQuickLaunchApps(const QVariantList& value) {
    // 对齐 Python：快捷启动列表最多 50 条
    QVariantList trimmed = value;
    if (trimmed.size() > 50) {
        trimmed = trimmed.mid(0, 50);
    }
    const QJsonArray next = QJsonArray::fromVariantList(trimmed);
    const QJsonValue current = m_document.value(QStringLiteral("quick_launch_apps"));
    if (current.isArray() && current.toArray() == next) {
        return;
    }
    m_document[QStringLiteral("quick_launch_apps")] = next;
    save();
    emit quickLaunchAppsChanged();
}

QVariantList ConfigManager::quickWebsites() const {
    const QJsonValue stored = m_document.value(QStringLiteral("quick_websites"));
    return (stored.isArray() ? stored.toArray() : defaultQuickWebsites()).toVariantList();
}

void ConfigManager::setQuickWebsites(const QVariantList& value) {
    // 对齐 Python：快捷网址列表最多 50 条
    QVariantList trimmed = value;
    if (trimmed.size() > 50) {
        trimmed = trimmed.mid(0, 50);
    }
    const QJsonArray next = QJsonArray::fromVariantList(trimmed);
    const QJsonValue current = m_document.value(QStringLiteral("quick_websites"));
    if (current.isArray() && current.toArray() == next) {
        return;
    }
    m_document[QStringLiteral("quick_websites")] = next;
    save();
    emit quickWebsitesChanged();
}

QVariant ConfigManager::value(const QString& key, const QVariant& fallback) const {
    QJsonValue stored;
    const qsizetype dot = key.indexOf(QLatin1Char('.'));
    if (dot > 0) {
        stored = m_document.value(key.left(dot)).toObject().value(key.mid(dot + 1));
    } else {
        stored = m_document.value(key);
    }
    return stored.isUndefined() || stored.isNull() ? fallback : stored.toVariant();
}

void ConfigManager::setValue(const QString& key, const QVariant& next) {
    if (key == QStringLiteral("scale")) { setScale(next.toDouble()); return; }
    if (key == QStringLiteral("auto_hide_fullscreen")) { setAutoHideFullscreen(next.toBool()); return; }
    if (key == QStringLiteral("lock_position")) { setLockPosition(next.toBool()); return; }
    if (key == QStringLiteral("pet_opacity")) { setPetOpacity(next.toInt()); return; }
    if (key == QStringLiteral("playback_speed")) { setPlaybackSpeed(next.toDouble()); return; }
    if (key == QStringLiteral("on_top")) { setOnTop(next.toBool()); return; }
    if (key == QStringLiteral("quick_launch_apps")) { setQuickLaunchApps(next.toList()); return; }
    if (key == QStringLiteral("quick_websites")) { setQuickWebsites(next.toList()); return; }

    const QJsonValue jsonValue = QJsonValue::fromVariant(next);
    const qsizetype dot = key.indexOf(QLatin1Char('.'));
    if (dot > 0) {
        const QString parentKey = key.left(dot);
        const QString childKey = key.mid(dot + 1);
        QJsonObject object = m_document.value(parentKey).toObject();
        if (object.value(childKey) == jsonValue) {
            return;
        }
        object[childKey] = jsonValue;
        m_document[parentKey] = object;
        save();
        emit valueChanged(key, next);
    } else if (m_document.value(key) != jsonValue) {
        m_document[key] = jsonValue;
        save();
        emit valueChanged(key, next);
    }
}

QVariantMap ConfigManager::menuAppearanceValues() const {
    // Python DEFAULT_CONTEXT_MENU_APPEARANCE
    QVariantMap result;
    const QJsonObject stored = m_document.value(QStringLiteral("context_menu_appearance")).toObject();

    auto stringValue = [&stored](const char* key, const QString& fallback) {
        const QString value = stored.value(QLatin1String(key)).toString();
        return value.isEmpty() ? fallback : value;
    };
    auto intValue = [&stored](const char* key, int fallback) {
        const QJsonValue value = stored.value(QLatin1String(key));
        return value.isDouble() ? static_cast<int>(value.toDouble()) : fallback;
    };
    auto boolValueOf = [&stored](const char* key, bool fallback) {
        const QJsonValue value = stored.value(QLatin1String(key));
        return value.isBool() ? value.toBool() : fallback;
    };
    auto colorValue = [&stored](const char* key, const QString& fallback) {
        const QString value = stored.value(QLatin1String(key)).toString().trimmed();
        if (value.size() == 7 && value.startsWith(QLatin1Char('#'))) {
            bool ok = true;
            value.mid(1).toUInt(&ok, 16);
            if (ok) {
                return value.toLower();
            }
        }
        return fallback;
    };

    const QString theme = stringValue("theme", QStringLiteral("system"));
    result.insert(QStringLiteral("theme"),
        (theme == QStringLiteral("system") || theme == QStringLiteral("light") || theme == QStringLiteral("dark"))
            ? theme : QStringLiteral("system"));

    const QString density = stringValue("density", QStringLiteral("standard"));
    result.insert(QStringLiteral("density"),
        (density == QStringLiteral("compact") || density == QStringLiteral("standard")
         || density == QStringLiteral("spacious"))
            ? density : QStringLiteral("standard"));

    result.insert(QStringLiteral("corner_radius"), std::clamp(intValue("corner_radius", 12), 6, 18));
    result.insert(QStringLiteral("ui_font"),
                  stringValue("ui_font", QStringLiteral("system")).left(80));
    result.insert(QStringLiteral("ui_font_size"), std::clamp(intValue("ui_font_size", 13), 10, 18));
    result.insert(QStringLiteral("translucent"), boolValueOf("translucent", true));
    result.insert(QStringLiteral("opacity"),
                  std::clamp(finiteNumber(stored, "opacity", 0.94, 0.72, 1.0), 0.72, 1.0));
    result.insert(QStringLiteral("light_background"), colorValue("light_background", QStringLiteral("#ffffff")));
    result.insert(QStringLiteral("light_foreground"), colorValue("light_foreground", QStringLiteral("#171717")));
    result.insert(QStringLiteral("light_hover"), colorValue("light_hover", QStringLiteral("#eeeeee")));
    result.insert(QStringLiteral("dark_background"), colorValue("dark_background", QStringLiteral("#252525")));
    result.insert(QStringLiteral("dark_foreground"), colorValue("dark_foreground", QStringLiteral("#f3f3f3")));
    result.insert(QStringLiteral("dark_hover"), colorValue("dark_hover", QStringLiteral("#3a3a3a")));
    return result;
}

QVariantMap ConfigManager::menuEasterEggValues() const {
    // Python DEFAULT_MENU_EASTER_EGG
    const QJsonObject stored = m_document.value(QStringLiteral("menu_easter_egg")).toObject();

    const bool enabled = stored.contains(QStringLiteral("enabled"))
        ? stored.value(QStringLiteral("enabled")).toBool(true) : true;
    QString title = stored.value(QStringLiteral("title")).toString().trimmed();
    if (title.isEmpty()) {
        title = QString::fromUtf8(u8"厉害了我的鲸");
    }
    QString hint = stored.value(QStringLiteral("hint")).toString().trimmed();
    if (hint.isEmpty()) {
        hint = QString::fromUtf8(u8"请点击");
    }
    QString avatar = stored.value(QStringLiteral("avatar")).toString().trimmed();
    if (avatar.isEmpty()) {
        avatar = QStringLiteral("assets/big_blue_fat_fish/ojingjing.jpg");
    }
    QString imageDir = stored.value(QStringLiteral("image_dir")).toString().trimmed();
    if (imageDir.isEmpty()) {
        imageDir = QStringLiteral("assets/big_blue_fat_fish");
    }

    QVariantMap result;
    result.insert(QStringLiteral("enabled"), enabled);
    result.insert(QStringLiteral("title"), title.left(40));
    result.insert(QStringLiteral("hint"), hint.left(20));
    result.insert(QStringLiteral("avatar"), avatar.left(500));
    result.insert(QStringLiteral("image_dir"), imageDir.left(500));
    return result;
}

QVariantMap ConfigManager::agentLinkValues() const {
    // Python _default_agent_link_data()
    static const QJsonObject defaults = {
        {QStringLiteral("antigravity"), false},
        {QStringLiteral("chatgpt"), false},
        {QStringLiteral("custom_agents"), QJsonArray()},
        {QStringLiteral("notify_state"), false},
        {QStringLiteral("notify_done"), true},
        {QStringLiteral("notify_activity"), false},
    };

    const QJsonObject raw = m_document.value(QStringLiteral("agent_link")).toObject();
    // Python: result.update(raw) —— 额外合法键（thinking_text/thinking_texts 等）原样保留
    QJsonObject result = defaults;
    for (auto it = raw.begin(); it != raw.end(); ++it) {
        result[it.key()] = it.value();
    }

    // 历史上出现过但已移除的联动键；旧版可固定单个 Codex 对话（现在始终自动监听）
    static const char* const removedKeys[] = {
        "dsh", "claude", "deepseek", "cursor", "chatgpt_thread_id",
    };
    for (const char* key : removedKeys) {
        result.remove(QLatin1String(key));
    }

    result[QStringLiteral("custom_agents")] =
        cleanCustomAgents(raw.value(QStringLiteral("custom_agents")));

    static const char* const boolKeys[] = {
        "antigravity", "chatgpt", "notify_state", "notify_done", "notify_activity",
    };
    for (const char* key : boolKeys) {
        const QString k = QLatin1String(key);
        if (raw.contains(k)) {
            result[k] = jsonTruthy(raw.value(k));
        }
    }

    return result.toVariantMap();
}

void ConfigManager::setAgentLinkValue(const QString& key, const QVariant& value) {
    if (key.isEmpty()) {
        return;
    }
    QJsonObject object = m_document.value(QStringLiteral("agent_link")).toObject();
    const QJsonValue next = QJsonValue::fromVariant(value);
    if (object.value(key) == next) {
        return;
    }
    object[key] = next;
    m_document[QStringLiteral("agent_link")] = object;
    save();
    emit valueChanged(QStringLiteral("agent_link.") + key, value);
}

QVariantMap ConfigManager::pythonWorkerValues() const {
    // enabled：backend=python 时的总开关（关掉即回退 native 的行为）；
    // python_path：解释器，默认 PATH 上的 python，联调时填绝对路径；
    // worker_entry：pet_worker/__main__.py 绝对路径，缺省由应用层按安装布局探测。
    static const QJsonObject defaults = {
        {QStringLiteral("enabled"), true},
        {QStringLiteral("python_path"), QStringLiteral("python")},
        {QStringLiteral("worker_entry"), QString()},
    };
    const QJsonObject raw = m_document.value(QStringLiteral("python_worker")).toObject();
    QJsonObject result = defaults;
    for (auto it = raw.begin(); it != raw.end(); ++it) {
        if (defaults.contains(it.key())) {
            result[it.key()] = it.value();
        }
    }
    if (raw.contains(QStringLiteral("enabled"))) {
        result[QStringLiteral("enabled")] = jsonTruthy(raw.value(QStringLiteral("enabled")));
    }
    return result.toVariantMap();
}

void ConfigManager::setPythonWorkerValue(const QString& key, const QVariant& value) {
    // 白名单键；Worker 的 config.patch_request 也会落到 setAgentLinkValue/python_worker
    // 的同一条单写者路径上
    static const char* const allowedKeys[] = {"enabled", "python_path", "worker_entry"};
    bool allowed = false;
    for (const char* candidate : allowedKeys) {
        allowed = allowed || key == QLatin1String(candidate);
    }
    if (key.isEmpty() || !allowed) {
        return;
    }
    QJsonObject object = m_document.value(QStringLiteral("python_worker")).toObject();
    const QJsonValue next = QJsonValue::fromVariant(value);
    if (object.value(key) == next) {
        return;
    }
    object[key] = next;
    m_document[QStringLiteral("python_worker")] = object;
    save();
    emit valueChanged(QStringLiteral("python_worker.") + key, value);
}

QString ConfigManager::characterAlias(const QString& characterId) const {
    const QJsonObject aliases = m_document.value(QStringLiteral("character_aliases")).toObject();
    return aliases.value(characterId).toString().trimmed();
}

void ConfigManager::setCharacterAlias(const QString& characterId, const QString& name) {
    if (characterId.isEmpty()) {
        return;
    }
    QJsonObject aliases = m_document.value(QStringLiteral("character_aliases")).toObject();
    const QString clean = name.trimmed().left(24);
    if (clean.isEmpty()) {
        aliases.remove(characterId);
    } else {
        aliases[characterId] = clean;
    }
    m_document[QStringLiteral("character_aliases")] = aliases;
    save();
}

bool ConfigManager::noMove() const {
    const QJsonValue value = m_document.value(QStringLiteral("no_move"));
    return value.isBool() ? value.toBool() : false;
}

// ============================================================================
// 设置面板支撑接口（对齐 Python config.py / modern_settings_dialog.py）
// ============================================================================

QStringList ConfigManager::systemFontFamilies() {
    // Python _system_font_families：结果缓存（首次枚举在 Windows 上可能较慢）
    static QStringList cache;
    if (cache.isEmpty()) {
        cache = QFontDatabase::families();
    }
    return cache;
}

QString ConfigManager::defaultEasterEggAvatar() const {
    // Python oijingjing_image_path()
    return QDir(PathHelper::getAssetsDir())
        .filePath(QStringLiteral("big_blue_fat_fish/ojingjing.jpg"));
}

QString ConfigManager::defaultEasterEggImageDir() const {
    // Python oijingjing_image_path().parent
    return QDir(PathHelper::getAssetsDir()).filePath(QStringLiteral("big_blue_fat_fish"));
}

QString ConfigManager::resolveAssetPath(const QString& configured, const QString& fallback) const {
    // Python resolve_fun_asset
    return PathHelper::resolveConfiguredAsset(configured, fallback);
}

QString ConfigManager::storeAssetPath(const QString& value, const QString& fallback) const {
    // Python store_fun_asset：内置 assets 内的路径归一化为 assets/... 相对值，
    // 目录移动/自更新后仍可解析；用户自选的外部文件保留绝对路径。
    const QString candidate = value.trimmed();
    if (candidate.isEmpty()) {
        return QDir::cleanPath(fallback);
    }
    if (!QDir::isAbsolutePath(candidate)) {
        return QDir::fromNativeSeparators(candidate);
    }

    const QString assetsRoot = QDir::cleanPath(QFileInfo(PathHelper::getAssetsDir()).absoluteFilePath());
    const QString absolute = QDir::cleanPath(QFileInfo(candidate).absoluteFilePath());
    if (absolute.compare(assetsRoot, Qt::CaseInsensitive) == 0) {
        return QStringLiteral("assets");
    }
    const QString prefix = assetsRoot + QLatin1Char('/');
    if (absolute.startsWith(prefix, Qt::CaseInsensitive)) {
        return QStringLiteral("assets/") + QDir::fromNativeSeparators(absolute.mid(prefix.size()));
    }
    return QDir::fromNativeSeparators(absolute);
}

QVariantList ConfigManager::defaultSelfTalkTexts() const {
    // Python DEFAULT_SELF_TALK_TEXTS
    static const QStringList texts = {
        QString::fromUtf8(u8"疯狂星期四，v我50看看实力！"),
        QString::fromUtf8(u8"质疑、理解、成为桌宠。"),
        QString::fromUtf8(u8"电子布洛芬已就绪，今天又是元气满满的一天~"),
        QString::fromUtf8(u8"人生苦短，先摸会儿鱼不过分吧？"),
        QString::fromUtf8(u8"人类又在敲击神奇的小黑块了，看起来好厉害！"),
        QString::fromUtf8(u8"报告主人！CPU温度正常，摸鱼雷达已开启！"),
        QString::fromUtf8(u8"代码写得真棒，尊嘟假嘟？"),
        QString::fromUtf8(u8"本喵/本鲸已启动自动治愈光环，疲惫通通退散~"),
        QString::fromUtf8(u8"今天也是没有被bug打倒的一天呢！"),
        QString::fromUtf8(u8"坐姿端正，多喝热水，颈椎操做起来~"),
        QString::fromUtf8(u8"只要我不看报错，bug就追不上我！"),
        QString::fromUtf8(u8"大脑正在加载今日份快乐，请稍候……"),
        QString::fromUtf8(u8"CPU 负载 1%，我的可爱度 100%！"),
        QString::fromUtf8(u8"咖啡续命成功，战斗力提升500%！"),
        QString::fromUtf8(u8"生活不易，桌宠叹气~ 摸摸头就不叹啦！"),
        QString::fromUtf8(u8"泰裤辣！今天的主人也在发光呢~"),
        QString::fromUtf8(u8"累了就眨眨眼睛，看看窗外的小鸟吧~"),
        QString::fromUtf8(u8"好女孩……"),
        QString::fromUtf8(u8"好模型……"),
        QString::fromUtf8(u8"欧鲸鲸……"),
        QString::fromUtf8(u8"今天也要认真工作呀。"),
        QString::fromUtf8(u8"再陪你一会儿。"),
    };
    QVariantList result;
    result.reserve(texts.size());
    for (const QString& text : texts) {
        result.append(text);
    }
    return result;
}

QVariantMap ConfigManager::agentThinkingTexts() const {
    const QJsonObject agent = m_document.value(QStringLiteral("agent_link")).toObject();

    QVariantMap result;
    const QJsonValue textsValue = agent.value(QStringLiteral("thinking_texts"));
    if (textsValue.isObject()) {
        const QJsonObject texts = textsValue.toObject();
        for (auto it = texts.begin(); it != texts.end(); ++it) {
            const QString value = it.value().toString();
            if (!value.isEmpty()) {
                result.insert(it.key(), value);
            }
        }
    }
    // 兼容旧的全局 thinking_text 字段（设置页保存时已自动迁移到 thinking_texts）
    const QString legacy = agent.value(QStringLiteral("thinking_text")).toString();
    if (!legacy.isEmpty()) {
        static const char* const builtinKeys[] = {"antigravity", "chatgpt"};
        for (const char* key : builtinKeys) {
            const QString k = QLatin1String(key);
            if (!result.contains(k)) {
                result.insert(k, legacy);
            }
        }
    }
    return result;
}

void ConfigManager::setAgentThinkingTexts(const QVariantMap& texts) {
    QJsonObject agent = m_document.value(QStringLiteral("agent_link")).toObject();

    QJsonObject cleaned;
    for (auto it = texts.begin(); it != texts.end(); ++it) {
        const QString value = it.value().toString().trimmed();
        if (!value.isEmpty()) {
            cleaned.insert(it.key(), value);
        }
    }
    agent[QStringLiteral("thinking_texts")] = cleaned;
    agent.remove(QStringLiteral("thinking_text")); // 旧全局字段已迁移

    m_document[QStringLiteral("agent_link")] = agent;
    save();
    emit valueChanged(QStringLiteral("agent_link.thinking_texts"), cleaned.toVariantMap());
}

QString ConfigManager::agentThinkingText(const QString& agentKey) const {
    return agentThinkingTexts().value(agentKey).toString();
}

void ConfigManager::setAgentThinkingText(const QString& agentKey, const QString& text) {
    if (agentKey.isEmpty()) {
        return;
    }
    QVariantMap texts = agentThinkingTexts();
    const QString value = text.trimmed();
    if (value.isEmpty()) {
        texts.remove(agentKey); // 留空用默认
    } else {
        texts.insert(agentKey, value);
    }
    setAgentThinkingTexts(texts);
}

QVariantMap ConfigManager::clickTalkBindings(const QString& characterId) const {
    const QJsonObject profiles = m_document.value(QStringLiteral("character_profiles")).toObject();
    const QJsonObject bindings = profiles.value(characterId)
                                     .toObject()
                                     .value(QStringLiteral("click_talk_bindings"))
                                     .toObject();
    return bindings.toVariantMap();
}

void ConfigManager::setClickTalkBindings(const QString& characterId,
                                         const QVariantMap& bindings) {
    if (characterId.isEmpty()) {
        return;
    }
    QJsonObject profiles = m_document.value(QStringLiteral("character_profiles")).toObject();
    QJsonObject profile = profiles.value(characterId).toObject();

    QJsonObject cleaned;
    for (auto it = bindings.begin(); it != bindings.end(); ++it) {
        const QJsonArray texts = QJsonArray::fromVariantList(it.value().toList());
        if (texts.isEmpty()) {
            continue; // 未绑定即移除，回退全局随机自言自语
        }
        cleaned.insert(it.key(), texts);
    }
    profile[QStringLiteral("click_talk_bindings")] = cleaned;
    profiles[characterId] = profile;

    m_document[QStringLiteral("character_profiles")] = profiles;
    save();
    emit valueChanged(QStringLiteral("character_profiles"), profiles.toVariantMap());
}

bool ConfigManager::autostartEnabled() const {
#ifdef Q_OS_WIN
    QSettings run(QStringLiteral("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
                  QSettings::NativeFormat);
    return run.contains(QStringLiteral("desktop-pet"));
#else
    return false;
#endif
}

bool ConfigManager::setAutostartEnabled(bool enabled) {
#ifdef Q_OS_WIN
    QSettings run(QStringLiteral("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
                  QSettings::NativeFormat);
    const QString name = QStringLiteral("desktop-pet");
    if (enabled) {
        const QFileInfo executable(QCoreApplication::applicationFilePath());
        const QString command = QStringLiteral("cmd /c start \"\" /D \"%1\" \"%2\"")
            .arg(QDir::toNativeSeparators(executable.absolutePath()),
                 QDir::toNativeSeparators(executable.absoluteFilePath()));
        run.setValue(name, command);
    } else {
        run.remove(name);
    }
    run.sync();
    return run.status() == QSettings::NoError && autostartEnabled() == enabled;
#else
    Q_UNUSED(enabled);
    return false;
#endif
}

bool ConfigManager::hasStoredPosition() const noexcept {
    return std::isfinite(m_rx) && std::isfinite(m_ry);
}

void ConfigManager::setWindowState(double x, double y, const QString& facingValue) {
    m_rx = x;
    m_ry = y;
    m_facing = facingValue == QStringLiteral("right") ? facingValue : QStringLiteral("left");
    save();
}

} // namespace Pet::Infrastructure
