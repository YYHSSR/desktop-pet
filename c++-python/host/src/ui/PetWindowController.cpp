#include "PetWindowController.hpp"
#include "ClickTalkBindingsDialog.hpp"
#include "ModernContextMenu.hpp"
#include "infrastructure/PathHelper.hpp"
#include <QGuiApplication>
#include <QCoreApplication>
#include <QBitmap>
#include <QCursor>
#include <QInputDialog>
#include <QLineEdit>
#include <QRandomGenerator>
#include <QRegion>
#include <QScreen>
#include <QSignalBlocker>
#include <QDateTime>
#include <QDesktopServices>
#include <QDir>
#include <QFileInfo>
#include <QProcess>
#include <QUrl>
#include <cmath>
#include <algorithm>

namespace Pet::UI {

PetWindowController::PetWindowController(Infrastructure::ConfigManager* config, QObject* parent)
    : QObject(parent)
    , m_config(config)
    , m_bodyTimer(new QTimer(this))
    , m_speechTimer(new QTimer(this))
    , m_wanderTimer(new QTimer(this))
    , m_fullscreenTimer(new QTimer(this))
    , m_selfTalkTimer(new QTimer(this))
    , m_animationGapTimer(new QTimer(this))
    , m_musicSingTimer(new QTimer(this)) {

    // 扫描角色库（内置 + 外部目录，对齐 Python external_character_dirs）
    m_catalog.scanCharacters(Infrastructure::PathHelper::getCharactersDir());
    m_catalog.scanExternalCharacters(Infrastructure::PathHelper::getDataDir() + QStringLiteral("/characters"));
    m_catalog.scanExternalCharacters(QDir(Infrastructure::PathHelper::getAppDir()).filePath(QStringLiteral("characters")));
    m_lastScale = scale();
    m_currentAnimPath = m_catalog.resolveAnimationPath(m_config->character(), Media::PetActionState::Idle);

    // 初始化 WebM 动画播放引擎
    m_player = std::make_unique<Media::WebMPlayer>(m_currentAnimPath, this);
    m_player->setPlaybackSpeed(m_config ? m_config->playbackSpeed() : 1.0);
    connect(m_player.get(), &Media::WebMPlayer::frameChanged, this, [this](int) {
        m_frameId++;
        emit frameIdChanged();
    });
    connect(m_player.get(), &Media::WebMPlayer::finished, this, &PetWindowController::onAnimFinished);
    m_player->start();

    // Python window.py：音乐自动唱歌 4s 轮询（music_detect.py 峰值检测）
    m_musicSingTimer->setInterval(4000);
    connect(m_musicSingTimer, &QTimer::timeout, this, &PetWindowController::onMusicSingTick);
    m_musicSingTimer->start();

    // 屏幕尺寸初始化：优先恢复到记忆的屏幕（对齐 Python screen_name），
    // 该屏不在线时退回主屏
    QScreen* target = QGuiApplication::primaryScreen();
    const QString rememberedScreen = m_config
        ? m_config->value(QStringLiteral("screen_name")).toString().trimmed() : QString();
    if (!rememberedScreen.isEmpty()) {
        const auto screens = QGuiApplication::screens();
        for (QScreen* candidate : screens) {
            if (candidate->name() == rememberedScreen) {
                target = candidate;
                break;
            }
        }
    }
    if (target) {
        const QRect geo = target->availableGeometry();
        if (m_config && m_config->hasStoredPosition()) {
            const double centerX = geo.left() + m_config->storedX() * geo.width();
            const double centerY = geo.top() + m_config->storedY() * geo.height();
            m_body.setPosition({centerX - 320.0 * scale(), centerY - 180.0 * scale()});
        } else {
            m_body.setPosition({geo.right() + 1.0 - 640.0 * scale() - 24.0,
                                geo.bottom() + 1.0 - 360.0 * scale()});
        }
    }
    m_facingRight = m_config && m_config->facing() == QStringLiteral("right");

    // 监听配置变更
    if (m_config) {
        connect(m_config, &Infrastructure::ConfigManager::characterChanged, this, [this](const QString& c) {
            m_currentAnimPath = m_catalog.resolveAnimationPath(c, Media::PetActionState::Idle);
            emit currentAnimPathChanged(m_currentAnimPath);
            emit currentCharacterChanged(c);
            bumpGeneration(QStringLiteral("character_changed")); // 动作目录随之失效
            if (m_player) {
                m_player->load(m_currentAnimPath);
            }
        });
        connect(m_config, &Infrastructure::ConfigManager::scaleChanged, this, [this](double s) {
            // 对齐 Python change_scale：缩放保持底边不动（左上角随高度差上下移动）
            const double oldHeight = 360.0 * m_lastScale;
            const double newHeight = 360.0 * s;
            if (std::abs(oldHeight - newHeight) > 0.5) {
                m_body.setPosition({m_body.position().x, m_body.position().y + oldHeight - newHeight});
                emit positionChanged();
            }
            m_lastScale = s;
            emit scaleChanged(s);
        });
        connect(m_config, &Infrastructure::ConfigManager::playbackSpeedChanged, this, [this](double speed) {
            if (m_player) m_player->setPlaybackSpeed(speed);
        });
        connect(m_config, &Infrastructure::ConfigManager::valueChanged, this,
                [this](const QString& key, const QVariant&) {
            if (key.startsWith(QStringLiteral("self_talk_"))) {
                scheduleSelfTalk();
            }
        });
    }

    connect(&m_catalog, &Media::CharacterCatalog::catalogUpdated, this, &PetWindowController::availableCharactersChanged);

    // 实体状态刷新（漫游插值 + Q 弹恢复）约 60 FPS
    connect(m_bodyTimer, &QTimer::timeout, this, &PetWindowController::onBodyTick);
    m_bodyTimer->setTimerType(Qt::PreciseTimer);
    m_bodyTimer->start(16);

    connect(m_speechTimer, &QTimer::timeout, this, &PetWindowController::onSpeechTimeout);
    m_selfTalkTimer->setSingleShot(true);
    connect(m_selfTalkTimer, &QTimer::timeout, this, &PetWindowController::onSelfTalkTimeout);
    scheduleSelfTalk();

    // 动作等待间歇（Python _animation_gap_timer）
    m_animationGapTimer->setSingleShot(true);
    connect(m_animationGapTimer, &QTimer::timeout, this, [this]() {
        m_animationGapActive = false;
    });

    // 闲置漫步定时器 (15 ~ 30 秒)
    connect(m_wanderTimer, &QTimer::timeout, this, &PetWindowController::onWanderTimer);
    m_wanderTimer->start(20000);

    // 全屏避让检测定时器 (1.5 秒)
    connect(m_fullscreenTimer, &QTimer::timeout, this, &PetWindowController::onFullscreenCheck);
    m_fullscreenTimer->start(1500);
}

PetWindowController::~PetWindowController() {
    savePosition();
}

QImage PetWindowController::currentImage() const {
    if (m_player) {
        return m_player->currentImage();
    }
    return QImage();
}

double PetWindowController::scale() const noexcept {
    return m_config ? m_config->scale() : 0.72;
}

void PetWindowController::setScale(double s) {
    if (m_config) {
        m_config->setScale(s);
    }
}

QStringList PetWindowController::availableCharacters() const {
    return m_catalog.availableCharacters();
}

QString PetWindowController::currentCharacter() const {
    return m_config ? m_config->character() : QStringLiteral("shenshen");
}

void PetWindowController::setCurrentCharacter(const QString& c) {
    if (m_config) {
        m_config->setCharacter(c);
    }
}

bool PetWindowController::isOpaqueAt(double localX, double localY) const {
    // 逐像素命中判定（对齐 Python 版 _is_transparent_at）：把窗口局部坐标换算
    // 成 640x360 帧坐标，alpha >= 16 视为命中角色本体。取不到当前帧时保守地
    // 返回命中，维持旧行为。
    if (!m_player) {
        return true;
    }
    const QImage frame = m_player->currentImage();
    if (frame.isNull()) {
        return true;
    }
    const double s = scale();
    if (s <= 0.0) {
        return true;
    }
    const int px = qBound(0, static_cast<int>(std::lround(localX / s)), frame.width() - 1);
    const int py = qBound(0, static_cast<int>(std::lround(localY / s)), frame.height() - 1);
    return frame.pixelColor(px, py).alpha() >= 16;
}

void PetWindowController::onPointerPressed(double screenX, double screenY, int button, int /*buttons*/, int /*modifiers*/) {
    // button: 1 = LeftButton, 2 = RightButton
    if (button == 2) {
        // 非拖拽状态的右键点击由 QML 处理弹出菜单
        return;
    }

    if (button == 1) {
        if (m_isWandering) {
            m_isWandering = false;
            m_wanderTimer->start(QRandomGenerator::global()->bounded(15000, 30001));
        }
        m_isPressCandidate = true;
        m_isDragging = false;
        m_pressPos = QPointF(screenX, screenY);
        m_grabOffset = QPointF(screenX - m_body.position().x, screenY - m_body.position().y);
    }
}

void PetWindowController::onPointerMoved(double screenX, double screenY, int /*buttons*/) {
    if (m_isPressCandidate) {
        double dx = screenX - m_pressPos.x();
        double dy = screenY - m_pressPos.y();
        if (std::hypot(dx, dy) < 5.0 * scale()) {
            return; // 未超点击阈值，仍保持候选
        }
        if (m_config && m_config->lockPosition()) {
            return;
        }
        // SHIFT+拖动模式：判定放在阈值处（对齐 Python），允许用户先按下再补按 SHIFT
        if (m_config && m_config->value(QStringLiteral("shift_drag"), false).toBool()
            && !(QGuiApplication::queryKeyboardModifiers() & Qt::ShiftModifier)) {
            return;
        }
        // 跨越阈值，正式启动拖拽
        m_isPressCandidate = false;
        m_isDragging = true;
        emit isDraggingChanged(true);
        bumpGeneration(QStringLiteral("drag_start"));
        switchAction(Media::PetActionState::Drag);
    }

    if (m_isDragging) {
        // 跟手平滑位移：精准保持抓取锚点偏移，杜绝坐标跳变
        m_body.setPosition({screenX - m_grabOffset.x(), screenY - m_grabOffset.y()});
        emit positionChanged();
    }
}

void PetWindowController::onPointerReleased(double screenX, double screenY, int button, int /*buttons*/) {
    Q_UNUSED(screenX);
    Q_UNUSED(screenY);

    if (button == 1) {
        if (m_isDragging) {
            m_isDragging = false;
            emit isDraggingChanged(false);
            bumpGeneration(QStringLiteral("drag_end"));
            // 拖动物理已移除：松手停在原地
            switchAction(Media::PetActionState::Idle);
            emit positionChanged();
            savePosition();
        } else if (m_isPressCandidate) {
            // 位移未达拖拽阈值：触发点击交互与台词（保留点击动作播放）
            m_isPressCandidate = false;
            triggerClickInteraction();
        }
    }
}

void PetWindowController::triggerClickInteraction() {
    // 点击可打断当前动画（含正在播放的点击回应与动作等待间歇），实现连续 Q 弹
    cancelAnimationGap();

    // 1. Q 弹果冻形变 (0.4 触发明显弹性)
    m_body.triggerSquash(0.4);
    emit squashChanged();

    // 3. 随机选择点击回应动画播放（若无点击回应素材则回退随机动作，一次性播放）
    auto cats = m_catalog.getAnimationCategories(currentCharacter());
    QStringList clickClips = cats.value(QStringLiteral("点击回应"));
    if (clickClips.isEmpty()) {
        clickClips = cats.value(QStringLiteral("随机动作"));
    }
    QString clickName;
    if (!clickClips.isEmpty()) {
        const int idx = QRandomGenerator::global()->bounded(clickClips.size());
        clickName = QFileInfo(clickClips.at(idx)).completeBaseName();
        playAnimation(clickClips.at(idx), /*loop=*/false);
    }

    // 4. 台词：仅当「点击触发自言自语」与「气泡自言自语」同时开启时弹出
    //    （对齐 Python window.py _on_click 的联动判定），
    //    优先播放当前点击动画绑定的专属台词，未绑定回退全局随机自言自语。
    if (m_config && m_config->value(QStringLiteral("click_show_self_talk"), false).toBool()
        && m_config->value(QStringLiteral("self_talk_enabled"), false).toBool()) {
        const QStringList bound =
            m_config->clickTalkBindings(currentCharacter()).value(clickName).toStringList();
        const int durationMs = selfTalkDurationMs();
        if (!bound.isEmpty()) {
            showSpeech(bound.at(QRandomGenerator::global()->bounded(bound.size())), durationMs);
        } else {
            showSpeech(randomSelfTalkText(), durationMs);
        }
    }
}

int PetWindowController::selfTalkDurationMs() const {
    // Python：点击台词与自言自语共用 self_talk_duration_seconds（原 C++ 硬编码 3000ms）
    const double seconds = m_config
        ? std::clamp(m_config->value(QStringLiteral("self_talk_duration_seconds"), 3.2).toDouble(), 1.0, 300.0)
        : 3.2;
    return static_cast<int>(seconds * 1000.0);
}

QString PetWindowController::randomSelfTalkText() const {
    QStringList texts;
    if (m_config) {
        const QVariantList configured = m_config->value(QStringLiteral("self_talk_texts"), QVariantList{}).toList();
        for (const QVariant& item : configured) {
            const QString text = item.toString().trimmed();
            if (!text.isEmpty()) texts.append(text);
        }
    }
    if (texts.isEmpty() && m_config) {
        // Python config.py：候选为空时回默认 22 条表
        const QVariantList defaults = m_config->defaultSelfTalkTexts();
        for (const QVariant& item : defaults) {
            const QString text = item.toString().trimmed();
            if (!text.isEmpty()) texts.append(text);
        }
    }
    if (texts.isEmpty()) {
        texts = {
            QString::fromUtf8(u8"今天也要元气满满呀~"),
            QString::fromUtf8(u8"我会一直陪着你的。"),
            QString::fromUtf8(u8"休息一下，看看远处吧。"),
            QString::fromUtf8(u8"正在认真守护你的桌面！")
        };
    }
    return texts.at(QRandomGenerator::global()->bounded(texts.size()));
}


void PetWindowController::scheduleSelfTalk(bool afterDisplay) {
    if (!m_config || !m_config->value(QStringLiteral("self_talk_enabled"), false).toBool()) {
        m_selfTalkTimer->stop();
        return;
    }
    const int minimum = std::clamp(m_config->value(QStringLiteral("self_talk_min_interval"), 20).toInt(), 5, 3600);
    const int maximum = std::clamp(m_config->value(QStringLiteral("self_talk_max_interval"), 60).toInt(), minimum, 3600);
    int delayMs = QRandomGenerator::global()->bounded(minimum, maximum + 1) * 1000;
    if (afterDisplay) {
        // Python _schedule_selftalk(after_display=True)：下一轮延迟加上显示时长
        const double duration = std::clamp(
            m_config->value(QStringLiteral("self_talk_duration_seconds"), 3.2).toDouble(), 1.0, 300.0);
        delayMs += static_cast<int>(duration * 1000.0);
        delayMs = std::max(delayMs, 1000);
    }
    m_selfTalkTimer->start(delayMs);
}

void PetWindowController::onSelfTalkTimeout() {
    if (QDateTime::currentMSecsSinceEpoch() < m_bubbleBusyUntilMs) {
        // 重要气泡占用中：本次自言自语跳过，重新排队下一次
        scheduleSelfTalk();
        return;
    }
    if (!m_config || !m_config->value(QStringLiteral("self_talk_enabled"), false).toBool()) {
        return;
    }
    const double duration =
        std::clamp(m_config->value(QStringLiteral("self_talk_duration_seconds"), 3.2).toDouble(), 1.0, 300.0);
    const int displayMs = static_cast<int>(duration * 1000.0);

    // Python _show_random_self_talk：把文字条目与图片文件拍平成一个列表等概率抽取
    const QStringList texts = [this]() {
        QStringList list;
        if (m_config) {
            const QVariantList configured =
                m_config->value(QStringLiteral("self_talk_texts"), QVariantList{}).toList();
            for (const QVariant& item : configured) {
                const QString text = item.toString().trimmed();
                if (!text.isEmpty()) list.append(text);
            }
            if (list.isEmpty()) {
                for (const QVariant& item : m_config->defaultSelfTalkTexts()) {
                    const QString text = item.toString().trimmed();
                    if (!text.isEmpty()) list.append(text);
                }
            }
        }
        return list;
    }();
    const QStringList images = selfTalkImages();
    const int total = texts.size() + images.size();
    if (total == 0) {
        scheduleSelfTalk();
        return;
    }
    const int pick = QRandomGenerator::global()->bounded(total);
    if (pick < texts.size()) {
        showSpeech(texts.at(pick), displayMs);
    } else if (!showSpeechImage(images.at(pick - texts.size()), displayMs)) {
        // 图片加载失败：本次不显示（Python show_image 返回 False 同样处理）
        scheduleSelfTalk();
        return;
    }
    // Python：自言自语显示期间同样占用「重要气泡」窗口（时长 + 1.5s），
    // Agent 的完成提醒在此期间让路
    holdBubble(displayMs / 1000.0 + 1.5);
    scheduleSelfTalk(/*afterDisplay=*/true);
}

bool PetWindowController::launchQuickApp(const QString& path, const QString& kind) {
    if (kind == QStringLiteral("default_browser")) {
        return QDesktopServices::openUrl(QUrl(QStringLiteral("https://www.google.com/")));
    }
    const QString target = QFileInfo(path.trimmed()).absoluteFilePath();
    if (target.isEmpty() || !QFileInfo::exists(target)) {
        return false;
    }
    if (QFileInfo(target).isDir()) {
        return QDesktopServices::openUrl(QUrl::fromLocalFile(target));
    }
    return QProcess::startDetached(target, {});
}

bool PetWindowController::openQuickWebsite(const QString& rawUrl) {
    QString url = rawUrl.trimmed();
    if (url.isEmpty()) {
        return false;
    }
    if (!url.startsWith(QStringLiteral("http://"), Qt::CaseInsensitive)
        && !url.startsWith(QStringLiteral("https://"), Qt::CaseInsensitive)
        && !url.startsWith(QStringLiteral("file://"), Qt::CaseInsensitive)) {
        url.prepend(QStringLiteral("https://"));
    }
    return QDesktopServices::openUrl(QUrl(url));
}

void PetWindowController::showSpeech(const QString& text, int durationMs) {
    m_speechText = text;
    if (!m_speechImage.isEmpty()) {
        m_speechImage.clear();
        emit speechImageChanged(m_speechImage);
    }
    resolveSpeechPlacement();
    m_speechVisible = true;
    emit speechTextChanged(m_speechText);
    emit speechVisibleChanged(true);
    m_speechTimer->start(durationMs);
}

bool PetWindowController::showSpeechImage(const QString& imagePath, int durationMs) {
    // Python speech_bubble.show_image：pixmap 为空时返回 False（本次不显示）
    const QFileInfo info(imagePath.trimmed());
    if (!info.isFile()) {
        return false;
    }
    m_speechText.clear();
    m_speechImage = QDir::fromNativeSeparators(info.absoluteFilePath());
    resolveSpeechPlacement();
    m_speechVisible = true;
    emit speechTextChanged(QString());
    emit speechImageChanged(m_speechImage);
    emit speechVisibleChanged(true);
    m_speechTimer->start(durationMs);
    return true;
}

QString PetWindowController::speechStyle() const {
    static const QStringList valid = {
        QStringLiteral("classic_top"), QStringLiteral("paper_left"),
        QStringLiteral("glass_right"), QStringLiteral("soft_blue_top"),
        QStringLiteral("breath_bubble")};
    const QString value = m_config
        ? m_config->value(QStringLiteral("self_talk_bubble_style"), QStringLiteral("classic_top")).toString()
        : QStringLiteral("classic_top");
    return valid.contains(value) ? value : QStringLiteral("classic_top");
}

void PetWindowController::resolveSpeechPlacement() {
    // Python bubble_rect_for_anchor 的候选顺序（[首选, top, top_left, top_right]）
    // 在窗口内气泡模型下的近似：气泡无法越出桌宠窗口，故把左/右候选映射为窗口内两侧，
    // 依次尝试「方案首选 → top → top_right → top_left」，取第一个整体落在屏幕可用区内者。
    const double winWidth = 640.0 * scale();
    const QString style = speechStyle();
    QString preferred = QStringLiteral("top");
    if (style == QLatin1String("paper_left") || style == QLatin1String("breath_bubble")) {
        preferred = QStringLiteral("top_left");
    } else if (style == QLatin1String("glass_right")) {
        preferred = QStringLiteral("top_right");
    }

    QScreen* screen = QGuiApplication::primaryScreen();
    const QRect avail = screen ? screen->availableGeometry() : QRect();
    const double petLeft = m_body.position().x;
    constexpr double bubbleWidth = 320.0;

    auto fits = [&](const QString& placement) {
        if (avail.isEmpty()) {
            return true;
        }
        double localX = 4.0;
        if (placement == QLatin1String("top")) {
            localX = (winWidth - bubbleWidth) / 2.0;
        } else if (placement == QLatin1String("top_right")) {
            localX = winWidth - bubbleWidth - 4.0;
        }
        const double left = petLeft + localX;
        return left >= avail.left() + 4.0 && left + bubbleWidth <= avail.right() - 3.0;
    };

    QString chosen = preferred;
    if (!fits(chosen)) chosen = QStringLiteral("top");
    if (!fits(chosen)) chosen = QStringLiteral("top_right");
    if (!fits(chosen)) chosen = QStringLiteral("top_left");
    if (chosen != m_speechPlacement) {
        m_speechPlacement = chosen;
        emit speechPlacementChanged(m_speechPlacement);
    }
    // 窗口内气泡模型暂不支持放到桌宠下方（Python 在顶部放不下时的回退）
    m_speechBelow = false;
    emit speechBelowChanged(m_speechBelow);
}

QStringList PetWindowController::selfTalkImages() const {
    // Python speech_bubble._list_self_talk_images：目录第一层、排序、无上限
    static const QStringList suffixes = {
        QStringLiteral("png"), QStringLiteral("jpg"), QStringLiteral("jpeg"),
        QStringLiteral("webp"), QStringLiteral("bmp"), QStringLiteral("gif"),
        QStringLiteral("tif"), QStringLiteral("tiff")};
    if (!m_config) {
        return {};
    }
    QString raw = m_config->value(QStringLiteral("self_talk_image_dir"),
                                  QStringLiteral("assets/big_blue_fat_fish")).toString().trimmed();
    if (raw.isEmpty()) {
        return {};
    }
    QDir dir(raw);
    if (dir.isRelative()) {
        // 相对路径按内置 assets 解析（Python resolve_fun_asset）
        dir = QDir(Infrastructure::PathHelper::getAssetsDir() + QLatin1Char('/') + raw);
    } else if (!dir.exists()) {
        return {}; // 外部目录被删 → 纯文本，不回退
    }
    if (!dir.exists()) {
        return {};
    }
    QStringList files;
    const QFileInfoList entries = dir.entryInfoList(QDir::Files, QDir::Name);
    for (const QFileInfo& entry : entries) {
        if (suffixes.contains(entry.suffix().toLower())) {
            files.append(entry.absoluteFilePath());
        }
    }
    return files;
}

void PetWindowController::onSpeechTimeout() {
    m_speechVisible = false;
    emit speechVisibleChanged(false);
}

void PetWindowController::switchAction(Media::PetActionState action) {
    QString path = m_catalog.resolveAnimationPath(m_config->character(), action);
    if (!path.isEmpty() && (path != m_currentAnimPath || (m_player && !m_player->loop()))) {
        m_currentAnimPath = path;
        emit currentAnimPathChanged(m_currentAnimPath);
        if (m_player) {
            bool loop = (action == Media::PetActionState::Idle);
            m_player->load(m_currentAnimPath, loop);
        }
    }
}

void PetWindowController::onBodyTick() {
    // 拖动物理已移除：这里只负责漫游插值与点击 Q 弹的形变恢复。
    if (m_isWandering) {
        if (m_config && m_config->value(QStringLiteral("no_move"), false).toBool()) {
            m_isWandering = false;
            switchAction(Media::PetActionState::Idle);
            m_wanderTimer->start(20000);
        } else {
            const double raw = m_wanderDurationSeconds > 0.0
                ? m_wanderClock.elapsed() / 1000.0 / m_wanderDurationSeconds : 1.0;
            const double t = std::clamp(raw, 0.0, 1.0);
            const bool smooth = !m_config
                || m_config->value(QStringLiteral("smooth_wander"), true).toBool();
            const double u = smooth ? t * t * (3.0 - 2.0 * t) : t;
            m_body.setPosition({
                m_wanderStart.x + (m_wanderTarget.x - m_wanderStart.x) * u,
                m_wanderStart.y + (m_wanderTarget.y - m_wanderStart.y) * u
            });
            emit positionChanged();
            if (t >= 1.0) {
                m_isWandering = false;
                switchAction(Media::PetActionState::Idle);
                m_wanderTimer->start(QRandomGenerator::global()->bounded(15000, 30001));
                savePosition();
            }
        }
    }

    if (!m_body.squashSettled()) {
        m_body.updateSquash(0.016);
        emit squashChanged();
    }
}

// ============================================================================
// Agent 联动运行时接口（对齐 Python PetWindow 的同名能力）
// ============================================================================

QStringList PetWindowController::animationNamesFor(const QString& folder) const {
    return m_catalog.animationNames(currentCharacter(), folder);
}

QHash<QString, QString> PetWindowController::actionCatalog() const {
    // action_id 形如 "<folder>/<动作名>"（folder: idle/turn/move/click/random），
    // 与 pet.snapshot 下发给 Worker 的动作白名单同源，绝不另起一套目录枚举
    static const QStringList folders = {
        QStringLiteral("idle"), QStringLiteral("turn"), QStringLiteral("move"),
        QStringLiteral("click"), QStringLiteral("random"),
    };
    QHash<QString, QString> catalog;
    for (const QString& folder : folders) {
        const QStringList names = animationNamesFor(folder);
        for (const QString& name : names) {
            if (!name.isEmpty()) {
                catalog.insert(folder + QLatin1Char('/') + name, name);
            }
        }
    }
    return catalog;
}

void PetWindowController::bumpGeneration(const QString& eventName) {
    // 代次递增即作废所有已发出的行为建议；action_finished 不递增（不改变建议有效性）
    ++m_generation;
    emit petEvent(eventName, m_generation);
}

bool PetWindowController::isOneShotPlaying() const {
    // Python _is_one_shot_playing：当前是否正在播一次性动作（动作池/点击回应/移动）。
    if (m_currentAnimPath.isEmpty()) {
        return false;
    }
    static const QStringList oneShotLabels = {
        QString::fromUtf8(u8"移动"), QString::fromUtf8(u8"点击回应"),
        QString::fromUtf8(u8"随机动作")};
    const QList<Media::AnimationCategory> categories =
        m_catalog.animationCategoriesOrdered(currentCharacter());
    for (const Media::AnimationCategory& category : categories) {
        if (oneShotLabels.contains(category.label)
            && category.files.contains(m_currentAnimPath)) {
            return true;
        }
    }
    return false;
}

void PetWindowController::playPendingLinkAnim() {
    const QString name = m_pendingLinkAnim;
    m_pendingLinkAnim.clear();
    if (name.isEmpty()) {
        return;
    }
    const QString resolved = m_catalog.resolveAnimationPath(currentCharacter(), name);
    if (resolved.isEmpty()) {
        return;
    }
    switchAnimation(name);
    m_linkAnimCurrentPath = resolved;
}

void PetWindowController::requestLinkAnim(const QString& animationName) {
    // Agent 联动动作请求：一次性动作播放中不打断，存为待播（最新覆盖旧的）。
    const QString name = animationName.trimmed();
    if (name.isEmpty()) {
        return;
    }
    m_pendingLinkAnim = name;
    if (!isOneShotPlaying()) {
        playPendingLinkAnim();
    }
}

void PetWindowController::requestLinkIdle() {
    // Agent 回到空闲：取消待播联动；一次性动作让它播完自然回待机，否则立即回待机。
    m_pendingLinkAnim.clear();
    m_linkAnimCurrentPath.clear();
    if (isOneShotPlaying()) {
        return;
    }
    switchAction(Media::PetActionState::Idle);
}

void PetWindowController::clearPendingLinkAnim() {
    m_pendingLinkAnim.clear();
}

void PetWindowController::setLinkNextAnimProvider(std::function<QString()> provider) {
    m_linkNextAnimProvider = std::move(provider);
}

QString PetWindowController::singAnimationPath() {
    // Python SING_ANIM = '悠闲哼歌'：在角色动画目录中按文件名查找
    if (!m_singAnimPath.isEmpty()) {
        return m_singAnimPath;
    }
    const QString singName = QString::fromUtf8(u8"悠闲哼歌");
    const QList<Media::AnimationCategory> categories =
        m_catalog.animationCategoriesOrdered(currentCharacter());
    for (const Media::AnimationCategory& category : categories) {
        for (const QString& file : category.files) {
            if (QFileInfo(file).completeBaseName() == singName) {
                m_singAnimPath = file;
                return m_singAnimPath;
            }
        }
    }
    return QString();
}

void PetWindowController::onMusicSingTick() {
    // Python window.py _check_music_sing：4s 轮询状态机
    if (!m_windowVisible) {
        return; // 隐藏时不检测
    }
    if (!m_config || !m_config->value(QStringLiteral("music_sing_enabled"), false).toBool()) {
        if (m_musicSingActive) {
            m_musicSingActive = false;
            switchAction(Media::PetActionState::Idle);
        }
        return;
    }
    const bool playing = m_musicDetector.isMusicPlaying();
    if (m_musicSingActive) {
        if (!playing) {
            m_musicSingActive = false;
            switchAction(Media::PetActionState::Idle); // 音乐停 → 恢复普通动画链
        }
        return; // 继续唱
    }
    if (m_isDragging || isOneShotPlaying()) {
        return; // 拖拽/一次性动作不打断
    }
    if (playing) {
        const QString path = singAnimationPath();
        if (path.isEmpty()) {
            return; // 角色没有唱歌动画素材
        }
        m_musicSingActive = true;
        cancelAnimationGap();
        playAnimation(path, /*loop=*/true);
    }
}

void PetWindowController::onAnimFinished() {
    // 语义事件不递增代次：动作播完不改变已发出建议的有效性
    emit petEvent(QStringLiteral("action_finished"), m_generation);
    // 对齐 Python _on_anim_ended：转向动画播完自动翻转朝向并存盘
    if (currentAnimationCategory() == QLatin1String("turn")) {
        m_facingRight = !m_facingRight;
        emit facingRightChanged(m_facingRight);
        savePosition();
    }
    // Python _on_anim_ended 的联动衔接插队逻辑：
    // 待播联动动作优先接上（平滑衔接，不打断刚播完的动作）；
    // 联动动作播完仍有 Agent 在忙则接下一个联动动作；否则走正常回待机流程。
    if (!m_pendingLinkAnim.isEmpty()) {
        playPendingLinkAnim();
        return;
    }
    if (!m_linkAnimCurrentPath.isEmpty() && m_currentAnimPath == m_linkAnimCurrentPath) {
        m_linkAnimCurrentPath.clear();
        if (m_linkNextAnimProvider) {
            const QString next = m_linkNextAnimProvider();
            if (!next.isEmpty()) {
                const QString resolved =
                    m_catalog.resolveAnimationPath(currentCharacter(), next);
                if (!resolved.isEmpty()) {
                    switchAnimation(next);
                    m_linkAnimCurrentPath = resolved;
                    return;
                }
            }
        }
    }
    if (m_isDragging || m_isWandering) {
        return;
    }

    const QString category = currentAnimationCategory();
    // Python：点击回应播完 → 取消间歇并立即回待机（支持连续点击）
    if (category == QLatin1String("click")) {
        cancelAnimationGap();
        switchAction(Media::PetActionState::Idle);
        return;
    }
    // Python：间歇进行中 → 继续播下一个待机/转向片段
    if (m_animationGapActive) {
        if (category == QLatin1String("idle") || category == QLatin1String("turn")) {
            playAnimationGapStep();
        } else {
            switchAction(Media::PetActionState::Idle);
        }
        return;
    }
    // Python：非待机动作（随机动作/移动）播完且配置了间隔 → 进入等待间歇
    const double gapSeconds = animationGapSeconds();
    if (gapSeconds > 0.0
        && (category == QLatin1String("random") || category == QLatin1String("move"))) {
        startAnimationGap();
        return;
    }
    // Python _on_anim_ended 末尾的 _pick_next()：0 秒间隔即「连续播放」
    pickNextAction();
}

// ============================================================================
// 动画链 / 移动 / 等待间歇（对齐 Python window.py）
// ============================================================================

QStringList PetWindowController::animationFilesFor(const QString& folder) const {
    static const QHash<QString, QString> labelOf = {
        {QStringLiteral("idle"), QString::fromUtf8(u8"待机")},
        {QStringLiteral("turn"), QString::fromUtf8(u8"转向")},
        {QStringLiteral("move"), QString::fromUtf8(u8"移动")},
        {QStringLiteral("click"), QString::fromUtf8(u8"点击回应")},
        {QStringLiteral("random"), QString::fromUtf8(u8"随机动作")},
    };
    const QString label = labelOf.value(folder);
    if (label.isEmpty()) {
        return {};
    }
    const QList<Media::AnimationCategory> categories =
        m_catalog.animationCategoriesOrdered(currentCharacter());
    for (const Media::AnimationCategory& category : categories) {
        if (category.label == label) {
            return category.files;
        }
    }
    return {};
}

QString PetWindowController::currentAnimationCategory() const {
    if (m_currentAnimPath.isEmpty()) {
        return QString();
    }
    const QList<Media::AnimationCategory> categories =
        m_catalog.animationCategoriesOrdered(currentCharacter());
    for (const Media::AnimationCategory& category : categories) {
        if (category.files.contains(m_currentAnimPath)) {
            if (category.label == QString::fromUtf8(u8"待机")) return QStringLiteral("idle");
            if (category.label == QString::fromUtf8(u8"转向")) return QStringLiteral("turn");
            if (category.label == QString::fromUtf8(u8"移动")) return QStringLiteral("move");
            if (category.label == QString::fromUtf8(u8"点击回应")) return QStringLiteral("click");
            if (category.label == QString::fromUtf8(u8"随机动作")) return QStringLiteral("random");
        }
    }
    return QString();
}

QString PetWindowController::pickFrom(const QStringList& pool, const QString& exclude) const {
    // Python _pick：优先排除当前项，池中只剩一项时回退整池
    QStringList entries;
    for (const QString& item : pool) {
        if (item != exclude) {
            entries.append(item);
        }
    }
    if (entries.isEmpty()) {
        entries = pool;
    }
    if (entries.isEmpty()) {
        return QString();
    }
    return entries.at(QRandomGenerator::global()->bounded(entries.size()));
}

void PetWindowController::playNamedAnimation(const QString& animationName, bool loop) {
    QString path = animationName;
    if (!QFileInfo::exists(path)) {
        path = m_catalog.resolveAnimationPath(currentCharacter(), animationName);
    }
    if (path.isEmpty()) {
        return;
    }
    playAnimation(path, loop);
}

double PetWindowController::animationGapSeconds() const {
    if (m_config == nullptr) {
        return 0.0;
    }
    return std::clamp(
        m_config->value(QStringLiteral("animation_gap_seconds"), 0.0).toDouble(), 0.0, 3600.0);
}

void PetWindowController::playAnimationGapStep() {
    // Python _play_animation_gap_step：从「待机 + 转向」池中随机播一个片段
    QStringList pool = animationFilesFor(QStringLiteral("idle"));
    pool += animationFilesFor(QStringLiteral("turn"));
    if (pool.isEmpty()) {
        return;
    }
    playAnimation(pickFrom(pool, m_currentAnimPath), /*loop=*/false);
}

void PetWindowController::startAnimationGap() {
    const double gapSeconds = animationGapSeconds();
    if (gapSeconds <= 0.0) {
        switchAction(Media::PetActionState::Idle);
        return;
    }
    m_animationGapActive = true;
    m_animationGapTimer->start(std::max(1, static_cast<int>(std::lround(gapSeconds * 1000.0))));
    playAnimationGapStep();
}

void PetWindowController::cancelAnimationGap() {
    m_animationGapTimer->stop();
    m_animationGapActive = false;
}

void PetWindowController::pickNextAction() {
    // Python _pick_next：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动
    const QStringList idles = animationFilesFor(QStringLiteral("idle"));
    const QStringList turns = animationFilesFor(QStringLiteral("turn"));
    const QStringList acts = animationFilesFor(QStringLiteral("random"));

    if (acts.isEmpty()) {
        // 角色包没有随机动作素材：需要 acts 的分支统一改走待机，绝不从空池取
        if (!idles.isEmpty()) {
            playNamedAnimation(pickFrom(idles, m_currentAnimPath), /*loop=*/true);
        }
        return;
    }

    const double roll = QRandomGenerator::global()->generateDouble();
    if (roll < 0.30) {
        playNamedAnimation(idles.isEmpty() ? pickFrom(acts, m_currentAnimPath)
                                           : pickFrom(idles, m_currentAnimPath),
                           /*loop=*/!idles.isEmpty());
    } else if (roll < 0.40) {
        playNamedAnimation(turns.isEmpty() ? pickFrom(acts, m_currentAnimPath)
                                           : pickFrom(turns, m_currentAnimPath),
                           /*loop=*/false);
    } else if (roll < 0.80) {
        playNamedAnimation(pickFrom(acts, m_currentAnimPath), /*loop=*/false);
    } else if (!tryMove()) {
        // 空间不足或「不移动」：概率并入动作
        playNamedAnimation(pickFrom(acts, m_currentAnimPath), /*loop=*/false);
    }
}

bool PetWindowController::tryMove() {
    // Python _try_move：朝 facing 移动；smart_edge_turn 下空间不足自动调头
    if (m_isWandering || m_isDragging) {
        return false;
    }
    if (m_config && m_config->value(QStringLiteral("no_move"), false).toBool()) {
        // 「不移动」模式：漫游动作池里不产生位移（对齐 Python _pick_next）
        return false;
    }
    QScreen* screen = currentScreen();
    if (screen == nullptr) {
        return false;
    }
    const QRect avail = screen->availableGeometry();
    const double halfWidth = 640.0 * scale() / 2.0;
    const double margin = 20.0; // Python catalog.MOVE_MARGIN
    const double centerX = m_body.position().x + halfWidth;
    const double leftBound = avail.left() + margin + halfWidth;
    const double rightBound = avail.right() - margin - halfWidth;
    if (rightBound <= leftBound) {
        return false;
    }

    int direction = m_facingRight ? 1 : -1;
    double availableForward = (direction == 1) ? (rightBound - centerX) : (centerX - leftBound);
    const double availableBackward = (direction == 1) ? (centerX - leftBound) : (rightBound - centerX);

    const bool smartEdgeTurn =
        m_config == nullptr
        || m_config->value(QStringLiteral("smart_edge_turn"), true).toBool();
    if (smartEdgeTurn && availableForward < 60.0 && availableBackward >= 60.0) {
        // Python catalog.MOVE_MIN_PX = 60
        m_facingRight = !m_facingRight;
        emit facingRightChanged(m_facingRight);
        direction = -direction;
        availableForward = availableBackward;
        const QStringList turns = animationFilesFor(QStringLiteral("turn"));
        if (!turns.isEmpty()) {
            playAnimation(turns.at(QRandomGenerator::global()->bounded(turns.size())),
                          /*loop=*/false);
        }
    }

    if (availableForward < 20.0) {
        return false;
    }
    const QStringList moves = animationFilesFor(QStringLiteral("move"));
    if (moves.isEmpty()) {
        return false;
    }

    // 自适应步长：MOVE_MIN_PX(60) ~ MOVE_MAX_PX(240)，并夹到可用范围内
    const double maxDistance = std::min(240.0, std::max(60.0, availableForward));
    const double minDistance = std::min(60.0, maxDistance);
    const double distance =
        minDistance + QRandomGenerator::global()->generateDouble() * (maxDistance - minDistance);
    const double targetCenterX =
        std::clamp(centerX + direction * distance, leftBound, rightBound);

    playNamedAnimation(pickFrom(moves, m_currentAnimPath), /*loop=*/false);

    m_wanderStart = m_body.position();
    m_wanderTarget = {targetCenterX - halfWidth, m_body.position().y};
    m_wanderDurationSeconds = std::clamp(distance / 85.0, 1.2, 8.0);
    m_wanderClock.restart();
    m_isWandering = true;
    m_wanderTimer->stop();
    return true;
}

void PetWindowController::holdBubble(double seconds) {
    // 声明重要气泡占用时长（自言自语在此期间让路）。
    m_bubbleBusyUntilMs = std::max(
        m_bubbleBusyUntilMs,
        QDateTime::currentMSecsSinceEpoch()
            + static_cast<qint64>(std::max(0.0, seconds) * 1000.0));
}

void PetWindowController::showBubble(const QString& text, int durationMs) {
    // Python show_bubble：向桌宠头顶冒泡提示（app 层反馈用，非侵入）。
    if (!m_windowVisible || text.isEmpty()) {
        return;
    }
    holdBubble(durationMs / 1000.0 + 2.0);
    showSpeech(text, durationMs);
}

void PetWindowController::hideBubble() {
    // Python win._speech_bubble.hide()：立即收起气泡（不清除占用窗口，
    // 与 Python 的实现保持一致）
    if (!m_speechVisible) {
        return;
    }
    m_speechVisible = false;
    m_speechTimer->stop();
    emit speechVisibleChanged(false);
}

void PetWindowController::setPetVisible(bool visible, bool notify) {
    if (m_windowVisible == visible) {
        return;
    }
    m_windowVisible = visible;
    if (!visible && notify) {
        // 用户主动隐藏（托盘/菜单）：全屏检测不得把它自动弹回（对齐 Python _auto_hidden）
        m_userHidden = true;
    } else if (visible) {
        m_userHidden = false;
        m_fsAutoHidden = false;
    }
    bumpGeneration(visible ? QStringLiteral("shown") : QStringLiteral("hidden"));
    if (!visible) {
        // Python _pause_activity：隐藏时全面暂停——动画/漫游/自言自语/哼歌全部停摆
        cancelAnimationGap();
        m_bodyTimer->stop();
        m_wanderTimer->stop();
        m_selfTalkTimer->stop();
        m_musicSingTimer->stop();
        hideBubble();
    } else {
        // 恢复：重启各周期任务
        m_bodyTimer->start(16);
        m_wanderTimer->start(20000);
        m_musicSingTimer->start(4000);
        scheduleSelfTalk();
    }
    emit windowVisibleChanged(visible);
    if (!visible && notify) {
        emit petHidden();
    }
}

void PetWindowController::onWanderTimer() {
    // 空闲看门狗：动画链在拖拽/抛掷等打断后若停摆，由这里重新起步。
    // 正常运行期间动画链会自我延续（Python _on_anim_ended → _pick_next）。
    if (m_isDragging || m_isWandering) {
        return;
    }
    pickNextAction();
    if (!m_isWandering) {
        m_wanderTimer->start(20000);
    }
}

void PetWindowController::onFullscreenCheck() {
    // 对齐 Python window.py：全屏检测不得覆盖用户手动隐藏的状态——
    // 只恢复「由本 watcher 隐藏」的桌宠，用户主动隐藏的保持隐藏
    if (m_userHidden) {
        return;
    }
    const bool isFs = m_config && m_config->autoHideFullscreen()
        ? Infrastructure::NativeWindowHelper::isForegroundWindowFullscreen() : false;
    if (isFs && m_windowVisible) {
        m_fsAutoHidden = true;
        setPetVisible(false);
    } else if (!isFs && !m_windowVisible && m_fsAutoHidden) {
        m_fsAutoHidden = false;
        setPetVisible(true);
    }
}

void PetWindowController::savePosition() {
    if (!m_config) return;
    QScreen* screen = QGuiApplication::screenAt(QPoint(
        static_cast<int>(m_body.position().x), static_cast<int>(m_body.position().y)));
    if (!screen) screen = QGuiApplication::primaryScreen();
    if (!screen) return;
    const QRect area = screen->availableGeometry();
    if (area.width() <= 0 || area.height() <= 0) return;
    const double centerX = m_body.position().x + 320.0 * scale();
    const double centerY = m_body.position().y + 180.0 * scale();
    const double rx = (centerX - area.left()) / area.width();
    const double ry = (centerY - area.top()) / area.height();
    m_config->setWindowState(rx, ry, m_facingRight ? QStringLiteral("right") : QStringLiteral("left"));
    // 对齐 Python：记忆所在屏幕，副屏上线时可恢复
    m_config->setValue(QStringLiteral("screen_name"), screen->name());
}

void PetWindowController::showContextMenu() {
    // 对齐 Python：每次打开菜单重新扫描角色（内置 + 外部目录都可能有新增）
    m_catalog.scanCharacters(Infrastructure::PathHelper::getCharactersDir());
    m_catalog.scanExternalCharacters(Infrastructure::PathHelper::getDataDir() + QStringLiteral("/characters"));
    m_catalog.scanExternalCharacters(QDir(Infrastructure::PathHelper::getAppDir()).filePath(QStringLiteral("characters")));
    ModernContextMenu::showContextMenu(this, m_config, QCursor::pos());
}

void PetWindowController::playAnimation(const QString& path, bool loop) {
    if (path.isEmpty()) return;
    m_currentAnimPath = path;
    emit currentAnimPathChanged(m_currentAnimPath);
    if (m_player) {
        m_player->load(m_currentAnimPath, loop);
    }
}

void PetWindowController::stopWandering() {
    if (m_isWandering) {
        m_isWandering = false;
        switchAction(Media::PetActionState::Idle);
        m_wanderTimer->start(20000);
    }
}

void PetWindowController::resetToBottomRight() {
    QScreen* screen = QGuiApplication::primaryScreen();
    if (!screen) return;
    const QRect avail = screen->availableGeometry();
    const double w = 640.0 * scale();
    const double h = 360.0 * scale();
    const double targetX = avail.right() - w - 24.0;   // Python CORNER_MARGIN = 24
    const double targetY = avail.bottom() - h;         // Python：底边贴任务栏上沿

    m_body.setPosition({targetX, targetY});
    emit positionChanged();
    savePosition();
}

void PetWindowController::hidePet() {
    // Python pet.hide()：用户主动隐藏 → 触发 on_hidden 托盘提示
    setPetVisible(false, true);
}

void PetWindowController::triggerEasterEgg() {
    showSpeech(QString::fromUtf8(u8"厉害了我的鲸！🐳"), 3500);
}

void PetWindowController::setPlaybackSpeed(double speed) {
    if (m_config) {
        m_config->setPlaybackSpeed(speed);
    }
    if (m_player) {
        m_player->setPlaybackSpeed(speed);
    }
}

// ============================================================================
// 右键菜单运行时接口
// ============================================================================

double PetWindowController::playbackSpeed() const noexcept {
    return m_config != nullptr ? m_config->playbackSpeed() : 1.0;
}

QRect PetWindowController::visibleContentRect() const {
    const double width = 640.0 * scale();
    const double height = 360.0 * scale();
    const QRect frame(qRound(m_body.position().x), qRound(m_body.position().y),
                      qRound(width), qRound(height));

    // 用当前帧的 alpha 包围盒近似 Python 的 visible_content_rect()：
    // 让菜单贴着角色而不是贴着透明画布。
    const QImage image = currentImage();
    if (image.isNull() || image.width() <= 0 || image.height() <= 0) {
        return frame;
    }
    const QRect bounds = QRegion(QBitmap::fromImage(image.createAlphaMask())).boundingRect();
    if (bounds.isEmpty()) {
        return frame;
    }
    const double scaleX = width / image.width();
    const double scaleY = height / image.height();
    return QRect(frame.topLeft()
                     + QPoint(qRound(bounds.x() * scaleX), qRound(bounds.y() * scaleY)),
                 QSize(qRound(bounds.width() * scaleX), qRound(bounds.height() * scaleY)));
}

QScreen* PetWindowController::currentScreen() const {
    const QPoint center(qRound(m_body.position().x + 320.0 * scale()),
                        qRound(m_body.position().y + 180.0 * scale()));
    QScreen* screen = QGuiApplication::screenAt(center);
    return screen != nullptr ? screen : QGuiApplication::primaryScreen();
}

void PetWindowController::restoreOnTopAfterContextMenu() {
    // Windows 下 QML 窗口的置顶由窗口标志维持，菜单关闭后无需重设层级；
    // 保留该接口用于对齐 Python 的菜单生命周期流程。
    if (m_config == nullptr || !m_config->onTop()) {
        return;
    }
}

void PetWindowController::switchAnimation(const QString& animationName) {
    if (m_config == nullptr || animationName.isEmpty()) {
        return;
    }
    const QString path = m_catalog.resolveAnimationPath(m_config->character(), animationName);
    if (!path.isEmpty()) {
        playAnimation(path, /*loop=*/false);
    }
}

void PetWindowController::triggerMoveAnimation(const QString& animationName) {
    // Python _trigger_move 会按 facing 方向位移；完整的移动计划属于行为对齐阶段，
    // 这里先走其贴边回退分支（原地播放走路姿态，不位移）。
    switchAnimation(animationName);
}

void PetWindowController::requestSwitchCharacter(const QString& characterId) {
    const QString clean = characterId.trimmed();
    if (m_config == nullptr || clean.isEmpty()) {
        return;
    }
    m_config->setCharacter(clean);
}

void PetWindowController::renameCharacter() {
    if (m_config == nullptr) {
        return;
    }
    const QString characterId = m_config->character();
    QString current = m_config->characterAlias(characterId);
    if (current.isEmpty()) {
        current = m_catalog.characterDisplayName(characterId);
    }

    bool accepted = false;
    const QString name = QInputDialog::getText(
        nullptr,
        QString::fromUtf8(u8"重命名角色"),
        QString::fromUtf8(u8"给 %1 起个名字（留空恢复默认）：").arg(characterId),
        QLineEdit::Normal, current, &accepted);
    if (!accepted) {
        return;
    }
    m_config->setCharacterAlias(characterId, name);

    QString shown = m_config->characterAlias(characterId);
    if (shown.isEmpty()) {
        shown = m_catalog.characterDisplayName(characterId);
    }
    showSpeech(QString::fromUtf8(u8"角色名：%1").arg(shown), 3000);
}

void PetWindowController::requestQuit() {
    // 菜单命令在 QMenu::exec() 返回后派发，已不在嵌套事件循环中，
    // 排到下一轮事件循环退出即可。
    QTimer::singleShot(0, QCoreApplication::instance(), []() {
        QCoreApplication::quit();
    });
}

void PetWindowController::setNoMove(bool enabled) {
    if (m_config == nullptr) {
        return;
    }
    m_config->setValue(QStringLiteral("no_move"), enabled);
    if (enabled && m_isWandering) {
        // 勾选瞬间正在漫步则立即停下回待机。
        stopWandering();
    }
}

void PetWindowController::setOnTopRuntime(bool enabled) {
    if (m_config == nullptr) {
        return;
    }
    m_config->setOnTop(enabled);
}

void PetWindowController::openModernSettings() {
    emit openSettingsRequested();
}

// ============================================================================
// 设置面板运行时支撑（台词绑定编辑）
// ============================================================================

QStringList PetWindowController::clickAnimationNames() const {
    // Python _discover_click_names：素材目录缺失时回退语义等价的动作名集合
    QStringList names = m_catalog.animationNames(currentCharacter(), QStringLiteral("click"));
    if (names.isEmpty()) {
        names = m_catalog.animationNames(currentCharacter(), QStringLiteral("random"));
    }
    return names;
}

void PetWindowController::openClickTalkBindings() const {
    if (m_config == nullptr) {
        return;
    }
    ClickTalkBindingsDialog dialog(m_config, clickAnimationNames(), nullptr);
    dialog.exec();
}

void PetWindowController::toggleAgentLink(const QString& agentKey, bool enabled, QAction* action) {
    // 混合架构：启停只写配置（agent_link.<key>），Worker 经快照自动跟随；
    // 宿主是唯一写者，这里没有第二个事件来源可以"设置失败"。
    if (m_config == nullptr || agentKey.isEmpty()) {
        return;
    }
    m_config->setAgentLinkValue(agentKey, enabled);
    if (enabled) {
        showBubble(QString::fromUtf8(u8"已开启 %1 状态联动监听～").arg(agentKey.toUpper()), 4000);
    }
}

void PetWindowController::setAgentLinkOption(const QString& key, bool enabled) {
    // 联动气泡提醒子项开关（开始干活 / 任务完成 / 过程汇报），立即写入配置。
    if (m_config == nullptr || key.isEmpty()) {
        return;
    }
    m_config->setAgentLinkValue(key, enabled);
}

} // namespace Pet::UI

