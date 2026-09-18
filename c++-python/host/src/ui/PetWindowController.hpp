#pragma once

#include "core/PetBody.hpp"
#include "infrastructure/ConfigManager.hpp"
#include "infrastructure/NativeWindowHelper.hpp"
#include "media/CharacterCatalog.hpp"
#include "media/MusicDetector.hpp"
#include "media/WebMPlayer.hpp"
#include "services/agent/IAgentLinkHost.hpp"

#include <QHash>
#include <QObject>
#include <QTimer>
#include <QPointF>
#include <QImage>
#include <QElapsedTimer>
#include <QRect>
#include <QStringList>
#include <QVariantList>
#include <functional>
#include <memory>
#include <vector>

class QAction;
class QScreen;

namespace Pet::UI {

class PetWindowController : public QObject, public Services::IAgentLinkHost {
    Q_OBJECT
    Q_PROPERTY(double posX READ posX NOTIFY positionChanged)
    Q_PROPERTY(double posY READ posY NOTIFY positionChanged)
    Q_PROPERTY(bool facingRight READ facingRight NOTIFY facingRightChanged)
    Q_PROPERTY(double scale READ scale WRITE setScale NOTIFY scaleChanged)
    Q_PROPERTY(double squashX READ squashX NOTIFY squashChanged)
    Q_PROPERTY(double squashY READ squashY NOTIFY squashChanged)
    Q_PROPERTY(QString currentAnimPath READ currentAnimPath NOTIFY currentAnimPathChanged)
    Q_PROPERTY(int frameId READ frameId NOTIFY frameIdChanged)
    Q_PROPERTY(QString speechText READ speechText NOTIFY speechTextChanged)
    Q_PROPERTY(bool speechVisible READ speechVisible NOTIFY speechVisibleChanged)
    Q_PROPERTY(QString speechImage READ speechImage NOTIFY speechImageChanged)
    Q_PROPERTY(QString speechStyle READ speechStyle CONSTANT)
    Q_PROPERTY(bool speechBelow READ speechBelow NOTIFY speechBelowChanged)
    Q_PROPERTY(QString speechPlacement READ speechPlacement NOTIFY speechPlacementChanged)
    Q_PROPERTY(bool isDragging READ isDragging NOTIFY isDraggingChanged)
    Q_PROPERTY(bool windowVisible READ windowVisible NOTIFY windowVisibleChanged)
    Q_PROPERTY(QStringList availableCharacters READ availableCharacters NOTIFY availableCharactersChanged)
    Q_PROPERTY(QString currentCharacter READ currentCharacter WRITE setCurrentCharacter NOTIFY currentCharacterChanged)

public:
    explicit PetWindowController(Infrastructure::ConfigManager* config, QObject* parent = nullptr);
    ~PetWindowController() override;

    double posX() const noexcept { return m_body.position().x; }
    double posY() const noexcept { return m_body.position().y; }
    bool facingRight() const noexcept { return m_facingRight; }
    double scale() const noexcept;
    void setScale(double s);

    double squashX() const noexcept { return m_body.squashScaleX(); }
    double squashY() const noexcept { return m_body.squashScaleY(); }
    QString currentAnimPath() const { return m_currentAnimPath; }
    int frameId() const noexcept { return m_frameId; }
    QImage currentImage() const;
    QString speechText() const { return m_speechText; }
    bool speechVisible() const noexcept { return m_speechVisible; }
    QString speechImage() const noexcept { return m_speechImage; }
    // Python speech_bubble.py set_style：非法值回退 classic_top
    QString speechStyle() const;
    bool speechBelow() const noexcept { return m_speechBelow; }
    QString speechPlacement() const noexcept { return m_speechPlacement; }
    bool isDragging() const noexcept { return m_isDragging; }
    bool windowVisible() const noexcept { return m_windowVisible; }

    QStringList availableCharacters() const;
    QString currentCharacter() const;
    void setCurrentCharacter(const QString& c);

    Media::CharacterCatalog& catalog() noexcept { return m_catalog; }
    const Media::CharacterCatalog& catalog() const noexcept { return m_catalog; }

    Q_INVOKABLE void showContextMenu();
    Q_INVOKABLE void playAnimation(const QString& path, bool loop = true);
    Q_INVOKABLE void resetToBottomRight();
    Q_INVOKABLE void hidePet();
    Q_INVOKABLE void triggerEasterEgg();
    Q_INVOKABLE void setPlaybackSpeed(double speed);
    Q_INVOKABLE void stopWandering();

    // ---- 右键菜单所需的运行时行为接口（对齐 Python PetWindow 的同名方法）----
    double playbackSpeed() const noexcept;
    // 角色可见内容矩形（全局坐标），供菜单避让定位使用。
    QRect visibleContentRect() const;
    QScreen* currentScreen() const;
    void restoreOnTopAfterContextMenu();

    // 播放指定动画名（Python _switch：一次性播放后回到待机）。
    void switchAnimation(const QString& animationName);
    // Python _trigger_move：完整位移逻辑属于行为对齐阶段，这里先走贴边回退分支。
    void triggerMoveAnimation(const QString& animationName);
    void requestSwitchCharacter(const QString& characterId);
    void renameCharacter();
    void requestQuit();
    void setNoMove(bool enabled);
    void setOnTopRuntime(bool enabled);
    void openModernSettings();
    // 失败/被拒时回滚菜单勾选态（Python _toggle_agent_link 的 action 回滚语义）。
    void toggleAgentLink(const QString& agentKey, bool enabled, QAction* action);
    void setAgentLinkOption(const QString& key, bool enabled);
    // ---- Agent 联动运行时（对齐 Python PetWindow 的同名能力）----
    // Python win.cats[folder]：指定分类（idle/turn/move/click/random）下的动画名
    [[nodiscard]] QStringList animationNamesFor(const QString& folder) const override;
    // Python request_link_anim：一次性动作播放中不打断，存为待播（最新覆盖旧的）
    void requestLinkAnim(const QString& animationName) override;
    // Python request_link_idle：取消待播联动；一次性动作让它播完自然回待机
    void requestLinkIdle() override;
    void clearPendingLinkAnim() override;
    // Python _on_anim_ended 的 win._link_next_provider 注入点
    void setLinkNextAnimProvider(std::function<QString()> provider) override;
    // Python _is_one_shot_playing
    [[nodiscard]] bool isOneShotPlaying() const;
    // Python show_bubble / hold_bubble / _bubble_busy_until
    void showBubble(const QString& text, int durationMs) override;
    void holdBubble(double seconds);
    // Python win._speech_bubble.hide()：托盘菜单弹出前让出气泡位
    void hideBubble();
    [[nodiscard]] qint64 bubbleBusyUntilMs() const noexcept override { return m_bubbleBusyUntilMs; }
    [[nodiscard]] bool petVisible() const noexcept override { return m_windowVisible; }
    // ---- 混合架构（C++ 宿主 + Python Worker）----
    // 宿主代次：角色切换/隐藏恢复/拖拽开始结束都递增。Worker 的行为建议必须回传
    // 生成时的代次，仲裁器据此把旧代次的建议作废。
    [[nodiscard]] quint64 generation() const noexcept { return m_generation; }
    // action_id → 动画名（"random/写代码" → "写代码"）。
    // 与 pet.snapshot 下发给 Worker 的动作白名单同源，绝不另起一套目录枚举。
    [[nodiscard]] QHash<QString, QString> actionCatalog() const;
    // 统一的可见性切换入口。
    // notify 对应 Python hide(notify=...)：仅用户主动隐藏时才提示托盘恢复入口。
    Q_INVOKABLE void setPetVisible(bool visible, bool notify = false);

    // ---- 设置面板运行时支撑（对齐 Python modern_settings_dialog.py）----
    // Python _discover_click_names：当前角色的点击动画名
    Q_INVOKABLE QStringList clickAnimationNames() const;
    // Python ClickTalkBindingsDialog 的编辑入口
    Q_INVOKABLE void openClickTalkBindings() const;

    Q_INVOKABLE void onPointerPressed(double screenX, double screenY, int button = 1, int buttons = 1, int modifiers = 0);
    Q_INVOKABLE void onPointerMoved(double screenX, double screenY, int buttons = 1);
    Q_INVOKABLE void onPointerReleased(double screenX, double screenY, int button = 1, int buttons = 0);
    // 逐像素命中判定（参照 Python 版 _is_transparent_at）：窗口局部坐标处
    // 的帧像素 alpha >= 16 视为命中角色本体；透明区域不触发点击/拖拽行为
    Q_INVOKABLE bool isOpaqueAt(double localX, double localY) const;

    Q_INVOKABLE void showSpeech(const QString& text, int durationMs = 4000);
    // Python speech_bubble.show_image：在气泡中显示图片；加载失败返回 false
    Q_INVOKABLE bool showSpeechImage(const QString& imagePath, int durationMs = 3200);
    // 台词显示时长（self_talk_duration_seconds，点击台词与自言自语共用）
    [[nodiscard]] int selfTalkDurationMs() const;
    Q_INVOKABLE void switchAction(Media::PetActionState action);
    Q_INVOKABLE void triggerClickInteraction();
    Q_INVOKABLE bool launchQuickApp(const QString& path, const QString& kind = {});
    Q_INVOKABLE bool openQuickWebsite(const QString& url);

signals:
    void openSettingsRequested();
    // Python win.on_hidden：用户主动隐藏桌宠（全屏自动隐藏不触发）
    void petHidden();
    void positionChanged();
    void facingRightChanged(bool facingRight);
    void scaleChanged(double scale);
    void squashChanged();
    void currentAnimPathChanged(const QString& path);
    void frameIdChanged();
    void speechTextChanged(const QString& text);
    void speechVisibleChanged(bool visible);
    void speechImageChanged(const QString& image);
    void speechBelowChanged(bool below);
    void speechPlacementChanged(const QString& placement);
    void isDraggingChanged(bool isDragging);
    void windowVisibleChanged(bool visible);
    void availableCharactersChanged();
    void currentCharacterChanged(const QString& character);
    // 低频语义事件：name ∈ click/drag_start/drag_end/action_finished/hidden/shown/
    // character_changed，generation 为事件发生时的宿主代次（action_finished 不递增）
    void petEvent(const QString& name, quint64 generation);

private slots:
    void onBodyTick();
    void onSpeechTimeout();
    void onWanderTimer();
    void onFullscreenCheck();
    void onSelfTalkTimeout();
    // Python window.py _check_music_sing（4s 轮询状态机）
    void onMusicSingTick();

private:
    // 递增代次并广播语义事件（代次变化即作废所有已发出的行为建议）
    void bumpGeneration(const QString& eventName);
    void savePosition();
    void scheduleSelfTalk(bool afterDisplay = false);
    QString randomSelfTalkText() const;
    // Python speech_bubble._list_self_talk_images：目录第一层图片（排序，无上限）
    QStringList selfTalkImages() const;
    // Python SING_ANIM（'悠闲哼歌'）动画素材路径；找不到返回空串
    [[nodiscard]] QString singAnimationPath();
    // Python bubble_rect_for_anchor 候选换位的窗口内近似：按屏幕边界选水平位置
    void resolveSpeechPlacement();
    // Python _on_anim_ended：一次性动作播完后的联动衔接与回待机
    void onAnimFinished();
    void playPendingLinkAnim();
    // ---- 动画链 / 移动 / 等待间歇（对齐 Python window.py）----
    // Python _pick_next：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动
    void pickNextAction();
    // Python _try_move：朝 facing 移动；smart_edge_turn 下空间不足自动调头
    bool tryMove();
    // Python _pick：从池中随机取一个（优先排除当前）
    QString pickFrom(const QStringList& pool, const QString& exclude) const;
    // 指定分类（idle/turn/move/click/random）下的动画文件绝对路径
    [[nodiscard]] QStringList animationFilesFor(const QString& folder) const;
    // 当前动画所属分类；未知返回空串
    [[nodiscard]] QString currentAnimationCategory() const;
    void playNamedAnimation(const QString& animationName, bool loop);
    [[nodiscard]] double animationGapSeconds() const;
    void startAnimationGap();
    void cancelAnimationGap();
    void playAnimationGapStep();

    Infrastructure::ConfigManager* m_config;
    Core::PetBody m_body;
    Media::CharacterCatalog m_catalog;
    std::unique_ptr<Media::WebMPlayer> m_player;

    QTimer* m_bodyTimer = nullptr;
    QTimer* m_speechTimer = nullptr;
    QTimer* m_wanderTimer = nullptr;
    QTimer* m_fullscreenTimer = nullptr;
    QTimer* m_selfTalkTimer = nullptr;
    QTimer* m_animationGapTimer = nullptr;

    int m_frameId = 0;
    bool m_facingRight = false;
    bool m_isDragging = false;
    bool m_isPressCandidate = false;
    bool m_speechVisible = false;
    bool m_windowVisible = true;
    QString m_currentAnimPath;
    QString m_speechText;
    QString m_speechImage;
    bool m_speechBelow = false;
    QString m_speechPlacement = QStringLiteral("top");
    // ---- 音乐自动唱歌（对齐 Python window.py music_sing）----
    QTimer* m_musicSingTimer = nullptr;
    bool m_musicSingActive = false;
    QString m_singAnimPath;
    Media::MusicDetector m_musicDetector;

    QPointF m_pressPos;
    QPointF m_grabOffset;

    bool m_isWandering = false;
    Core::Vector2D m_wanderStart;
    Core::Vector2D m_wanderTarget;
    QElapsedTimer m_wanderClock;
    double m_wanderDurationSeconds = 0.0;

    // Agent 联动动作衔接状态（Python _pending_link_anim / _link_anim_current）
    QString m_pendingLinkAnim;
    QString m_linkAnimCurrentPath;
    std::function<QString()> m_linkNextAnimProvider;
    // Python _bubble_busy_until：重要气泡占用期间自言自语让路
    qint64 m_bubbleBusyUntilMs = 0;
    // 宿主代次：角色切换/隐藏恢复/拖拽开始结束递增
    quint64 m_generation = 0;
    // 全屏自动隐藏守卫（对齐 Python _auto_hidden）：
    // m_userHidden = 用户主动隐藏，全屏检测不得弹回；m_fsAutoHidden = 由检测器隐藏
    bool m_userHidden = false;
    bool m_fsAutoHidden = false;
    // 缩放保持底边锚定用的上一帧 scale（对齐 Python change_scale）
    double m_lastScale = 0.72;

    // Python _animation_gap_active：动作等待间歇进行中
    bool m_animationGapActive = false;
};

} // namespace Pet::UI
