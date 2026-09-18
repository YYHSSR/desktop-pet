#include "MenuPrimitives.hpp"

#include "MenuIcons.hpp"
#include "MenuStyle.hpp"
#include "media/AnimationThumbnail.hpp"

#include <QActionGroup>
#include <QFileInfo>
#include <QHash>
#include <QIcon>
#include <QMetaObject>
#include <QPixmap>
#include <QPointer>
#include <QSet>
#include <QThreadPool>
#include <QTimer>
#include <algorithm>
#include <cmath>

namespace Pet::UI {

namespace {

// -------------------------------------------------- 延迟回调（Python 的 _deferred_callbacks）

// 对应 Python 挂在根菜单上的 _deferred_callbacks：菜单销毁时自动清理。
QHash<QMenu*, std::vector<std::function<void()>>> g_deferredCallbacks;

std::vector<std::function<void()>>* callbackStore(QMenu* menu, bool create) {
    const auto it = g_deferredCallbacks.find(menu);
    if (it != g_deferredCallbacks.end()) {
        return &it.value();
    }
    if (!create) {
        return nullptr;
    }
    auto inserted = g_deferredCallbacks.insert(menu, {});
    // 无 context 的连接：菜单销毁时仅用其指针作为键做清理。
    QObject::connect(menu, &QObject::destroyed, [menu]() {
        g_deferredCallbacks.remove(menu);
    });
    return &inserted.value();
}

QMenu* rootMenu(QMenu* menu) {
    QMenu* root = menu;
    while (auto* parentMenu = qobject_cast<QMenu*>(root->parent())) {
        root = parentMenu;
    }
    return root;
}

// -------------------------------------------------- 动画首帧缩略图缓存

constexpr int kAnimationIconCacheLimit = 128;
QHash<QString, QImage> g_animationIconCache;

QString animationCacheKey(const QString& character, const QString& animationName) {
    return character + QLatin1Char('\n') + animationName;
}

QImage cachedAnimationIcon(const QString& character, const QString& animationName) {
    return g_animationIconCache.value(animationCacheKey(character, animationName));
}

void storeAnimationIcon(const QString& character, const QString& animationName, const QImage& image) {
    if (g_animationIconCache.size() >= kAnimationIconCacheLimit && !g_animationIconCache.isEmpty()) {
        g_animationIconCache.erase(g_animationIconCache.begin());
    }
    g_animationIconCache.insert(animationCacheKey(character, animationName), image);
}

} // namespace

// 动画分类子菜单的缩略图加载器：最多 2 个并发解码，
// 解码结果只在子菜单不可见时写回 QAction（Python 的既有约束）。
class AnimationIconController : public QObject {
    Q_OBJECT
public:
    AnimationIconController(QMenu* submenu, const QString& character,
                            const QList<QPair<QAction*, QString>>& iconActions,
                            const QList<QPair<QAction*, QString>>& lazyActions,
                            const QHash<QString, QString>& pathByName,
                            QObject* parent)
        : QObject(parent)
        , m_submenu(submenu)
        , m_character(character)
        , m_iconActions(iconActions)
        , m_pending(lazyActions)
        , m_pathByName(pathByName) {
        m_pool = new QThreadPool(this);
        m_pool->setMaxThreadCount(2);
    }

    ~AnimationIconController() override {
        if (m_pool != nullptr) {
            // 清掉尚未启动的任务，避免析构时长时间等待。
            m_pool->clear();
        }
    }

    void start() {
        pump();
    }

public slots:
    void refreshCachedIcons() {
        if (m_submenu == nullptr || m_submenu->isVisible()) {
            return;
        }
        for (const auto& entry : m_iconActions) {
            const QImage image = cachedAnimationIcon(m_character, entry.second);
            if (!image.isNull()) {
                entry.first->setIcon(MenuIcons::fittedPetPixmapIcon(
                    m_submenu, QPixmap::fromImage(image)));
            }
        }
    }

    void onThumbnailReady(const QString& animationName, const QImage& image) {
        if (m_running > 0) {
            --m_running;
        }
        if (m_submenu != nullptr && !image.isNull()) {
            QAction* action = m_byName.value(animationName, nullptr);
            if (action != nullptr) {
                // 对可见且可滚动的菜单反复 setIcon 会破坏条目几何与滚动布局。
                if (!m_submenu->isVisible()) {
                    action->setIcon(MenuIcons::fittedPetPixmapIcon(
                        m_submenu, QPixmap::fromImage(image)));
                    m_submenu->update();
                }
            }
            storeAnimationIcon(m_character, animationName, image);
        }
        pump();
    }

private:
    void pump() {
        while (m_running < 2 && !m_pending.isEmpty()) {
            const QPair<QAction*, QString> entry = m_pending.takeFirst();
            if (m_requested.contains(entry.second)) {
                continue;
            }
            m_requested.insert(entry.second);
            launch(entry.first, entry.second);
        }
    }

    void launch(QAction* action, const QString& animationName) {
        const QString path = m_pathByName.value(animationName);
        if (path.isEmpty()) {
            return;
        }
        m_byName.insert(animationName, action);
        ++m_running;

        QPointer<AnimationIconController> guard(this);
        const QString character = m_character;
        m_pool->start(QRunnable::create([guard, path, animationName, character]() {
            const QImage image = Media::AnimationThumbnail::decodeRepresentativeFrame(path);
            if (guard.isNull()) {
                return;
            }
            QMetaObject::invokeMethod(guard.data(), [guard, animationName, image]() {
                if (!guard.isNull()) {
                    guard->onThumbnailReady(animationName, image);
                }
            }, Qt::QueuedConnection);
        }));
    }

    QMenu* m_submenu = nullptr;
    QString m_character;
    QThreadPool* m_pool = nullptr;
    QList<QPair<QAction*, QString>> m_iconActions;
    QList<QPair<QAction*, QString>> m_pending;
    QHash<QString, QAction*> m_byName;
    QHash<QString, QString> m_pathByName;
    QSet<QString> m_requested;
    int m_running = 0;
};

// ---------------------------------------------------------------- 通用原语

void takeDeferredMenuCallbacks(QMenu* menu, std::vector<std::function<void()>>& out) {
    if (menu == nullptr) {
        return;
    }
    std::vector<std::function<void()>>* store = callbackStore(menu, false);
    if (store == nullptr) {
        return;
    }
    out = *store;
    store->clear();
}

bool deferMenuCallback(QMenu* menu, std::function<void()> callback) {
    if (menu == nullptr) {
        return false;
    }
    if (!menu->isVisible()) {
        callback();
        return false;
    }
    QMenu* root = rootMenu(menu);
    callbackStore(root, true)->push_back(std::move(callback));
    root->close();
    return true;
}

void connectAction(QAction* action, std::function<void()> callback) {
    if (action == nullptr) {
        return;
    }
    QObject::connect(action, &QAction::triggered, action,
                     [action, callback = std::move(callback)](bool) {
        auto* menu = qobject_cast<QMenu*>(action->parent());
        if (menu != nullptr && action->property("closeOnTrigger").toBool() && menu->isVisible()) {
            deferMenuCallback(menu, callback);
            return;
        }
        callback();
    });
}

QAction* addMenuAction(QMenu* menu, const QString& text, const QString& iconName,
                       std::function<void()> callback, bool closeOnTrigger) {
    QAction* action = menu->addAction(
        iconName.isEmpty() ? QIcon() : MenuIcons::vectorMenuIcon(menu, iconName), text);
    action->setProperty("closeOnTrigger", closeOnTrigger);
    if (callback) {
        connectAction(action, std::move(callback));
    }
    return action;
}

QMenu* addMenuSubmenu(QMenu* menu, const QString& text, const QString& iconName) {
    auto* submenu = new QMenu(text, menu);
    menu->addMenu(submenu);
    inheritMenuStyle(menu, submenu);
    if (!iconName.isEmpty()) {
        submenu->setIcon(MenuIcons::vectorMenuIcon(menu, iconName));
    }
    return submenu;
}

// ---------------------------------------------------------------- 播放动画分类

namespace {

void populateAnimationCategory(QMenu* submenu, const AnimationCategorySpec& spec,
                               const QString& character, bool leafRoleIcons) {
    if (submenu->property("animationPopulated").toBool()) {
        return;
    }
    submenu->setProperty("animationPopulated", true);

    QList<QPair<QAction*, QString>> iconActions;
    QList<QPair<QAction*, QString>> lazyActions;
    QHash<QString, QString> pathByName;

    for (const QString& file : spec.files) {
        const QString name = QFileInfo(file).completeBaseName();
        QAction* action = leafRoleIcons
            ? submenu->addAction(QIcon(), name)
            : submenu->addAction(name);
        connectAction(action, [spec, name]() { spec.callback(name); });
        if (!leafRoleIcons) {
            continue;
        }
        pathByName.insert(name, file);
        iconActions.append({action, name});

        const QImage cached = cachedAnimationIcon(character, name);
        if (!cached.isNull()) {
            action->setIcon(MenuIcons::fittedPetPixmapIcon(submenu, QPixmap::fromImage(cached)));
        } else {
            // 中性占位图标避免文字列在解码完成时跳动。
            action->setIcon(MenuIcons::vectorMenuIcon(submenu, QStringLiteral("loading")));
            lazyActions.append({action, name});
        }
    }

    if (lazyActions.isEmpty()) {
        return;
    }

    auto* controller = new AnimationIconController(
        submenu, character, iconActions, lazyActions, pathByName, submenu);
    QObject::connect(submenu, &QMenu::aboutToShow,
                     controller, &AnimationIconController::refreshCachedIcons);
    QObject::connect(submenu, &QMenu::aboutToHide, controller, [controller]() {
        QTimer::singleShot(0, controller, &AnimationIconController::refreshCachedIcons);
    });
    controller->start();
}

} // namespace

void buildAnimationCategories(QMenu* menu, const QList<AnimationCategorySpec>& categories,
                              const QString& character,
                              bool legacyLabels, bool leafRoleIcons) {
    for (const AnimationCategorySpec& spec : categories) {
        if (spec.files.isEmpty()) {
            continue;
        }
        const QString label = legacyLabels
            ? QStringLiteral("动画 · %1").arg(spec.label)
            : spec.label;
        auto* submenu = new QMenu(label, menu);
        menu->addMenu(submenu);
        inheritMenuStyle(menu, submenu);

        // 首次展开该分类子菜单时才填充动作，避免根菜单构建时遍历全部动画。
        QObject::connect(submenu, &QMenu::aboutToShow, submenu,
                         [submenu, spec, character, leafRoleIcons]() {
            populateAnimationCategory(submenu, spec, character, leafRoleIcons);
        });
    }
}

// ---------------------------------------------------------------- 播放速率 / 大小 / 角色

QMenu* buildSpeedMenu(QMenu* menu, const MenuContext& context) {
    QMenu* submenu = addMenuSubmenu(menu, QString::fromUtf8(u8"播放速率"),
                                    QStringLiteral("speed"));
    auto* group = new QActionGroup(submenu);
    group->setExclusive(true);

    const double current = context.controller != nullptr ? context.controller->playbackSpeed() : 1.0;
    for (int index = 10; index <= 20; ++index) {
        const double value = index / 10.0;
        QAction* action = submenu->addAction(QStringLiteral("%1x").arg(value, 0, 'f', 1));
        action->setCheckable(true);
        action->setChecked(std::abs(current - value) < 0.01);
        group->addAction(action);
        connectAction(action, [context, value]() {
            if (context.controller != nullptr) {
                context.controller->setPlaybackSpeed(value);
            }
        });
    }
    return submenu;
}

QMenu* buildSizeMenu(QMenu* menu, const MenuContext& context) {
    QMenu* submenu = addMenuSubmenu(menu, QString::fromUtf8(u8"大小"), QStringLiteral("size"));
    auto* group = new QActionGroup(submenu);
    group->setExclusive(true);

    // Python catalog.SCALE_STEPS = (0.5, 0.72, 0.85, 1.0)，标签为 CANVAS_W * scale。
    static const double kScaleSteps[] = {0.5, 0.72, 0.85, 1.0};
    const double current = context.controller != nullptr ? context.controller->scale() : 0.72;
    for (double scale : kScaleSteps) {
        QAction* action = submenu->addAction(
            QStringLiteral("%1px").arg(qRound(640.0 * scale)));
        action->setCheckable(true);
        action->setChecked(std::abs(current - scale) < 0.02);
        group->addAction(action);
        connectAction(action, [context, scale]() {
            if (context.controller != nullptr) {
                context.controller->setScale(scale);
            }
        });
    }
    return submenu;
}

QMenu* buildCharacterMenu(QMenu* menu, const MenuContext& context) {
    QMenu* submenu = addMenuSubmenu(menu, QString::fromUtf8(u8"切换角色"),
                                    QStringLiteral("character"));
    auto* group = new QActionGroup(submenu);
    group->setExclusive(true);

    const QString current = context.config != nullptr ? context.config->character() : QString();
    const QStringList characters = context.controller != nullptr
        ? context.controller->availableCharacters()
        : QStringList{};

    for (const QString& characterId : characters) {
        QString label;
        if (context.config != nullptr) {
            label = context.config->characterAlias(characterId);
        }
        if (label.isEmpty() && context.controller != nullptr) {
            label = context.controller->catalog().characterDisplayName(characterId);
        }
        if (label.isEmpty()) {
            label = characterId;
        }

        QAction* action = submenu->addAction(label);
        action->setCheckable(true);
        action->setChecked(characterId == current);
        group->addAction(action);
        action->setProperty("closeOnTrigger", true);
        connectAction(action, [context, characterId]() {
            if (context.controller != nullptr) {
                context.controller->requestSwitchCharacter(characterId);
            }
        });
    }

    if (context.controller != nullptr) {
        submenu->addSeparator();
        addMenuAction(submenu, QString::fromUtf8(u8"重命名当前角色…"), {}, [context]() {
            if (context.controller != nullptr) {
                context.controller->renameCharacter();
            }
        }, true);
    }
    return submenu;
}

// ---------------------------------------------------------------- 功能开关

QAction* addReturnCorner(QMenu* menu, const MenuContext& context) {
    return addMenuAction(menu, QString::fromUtf8(u8"回到右下角"), QStringLiteral("corner"),
                         [context]() {
        if (context.controller != nullptr) {
            context.controller->resetToBottomRight();
        }
    });
}

QAction* addHidePet(QMenu* menu, const MenuContext& context) {
    // 隐藏后菜单随之关闭，避免菜单悬空无法找回桌宠。
    return addMenuAction(menu, QString::fromUtf8(u8"隐藏桌宠"), QStringLiteral("hide"),
                         [context]() {
        if (context.controller != nullptr) {
            context.controller->hidePet();
        }
    }, true);
}

QAction* addNoMove(QMenu* menu, const MenuContext& context) {
    QAction* action = addMenuAction(menu, QString::fromUtf8(u8"不移动"), QStringLiteral("pause"));
    action->setCheckable(true);
    action->setChecked(context.config != nullptr && context.config->noMove());
    QObject::connect(action, &QAction::toggled, menu, [context](bool enabled) {
        if (context.controller != nullptr) {
            context.controller->setNoMove(enabled);
        }
    });
    return action;
}

QAction* addOnTop(QMenu* menu, const MenuContext& context) {
    QAction* action = addMenuAction(menu, QString::fromUtf8(u8"窗口置顶"), QStringLiteral("pin"));
    action->setCheckable(true);
    action->setChecked(context.config == nullptr || context.config->onTop());
    QObject::connect(action, &QAction::toggled, menu, [context](bool enabled) {
        if (context.controller != nullptr) {
            context.controller->setOnTopRuntime(enabled);
        }
    });
    return action;
}

QAction* addAutostart(QMenu* menu, const MenuContext& context) {
    QAction* action = addMenuAction(menu, QString::fromUtf8(u8"开机自启"),
                                   QStringLiteral("autostart"));
    action->setCheckable(true);
    action->setChecked(context.config != nullptr && context.config->autostartEnabled());
    QObject::connect(action, &QAction::toggled, menu, [context](bool enabled) {
        if (context.config == nullptr) {
            return;
        }
        context.config->setAutostartEnabled(enabled);
        context.config->setValue(QStringLiteral("autostart_wanted"), enabled);
    });
    return action;
}

QMenu* addAgentLinkMenu(QMenu* menu, const MenuContext& context) {
    if (context.controller == nullptr) {
        return nullptr;
    }
    QMenu* submenu = addMenuSubmenu(menu, QString::fromUtf8(u8"Agent 联动"),
                                    QStringLiteral("agent"));
    const QVariantMap values = context.config != nullptr
        ? context.config->agentLinkValues()
        : QVariantMap();

    // 内置联动 Agent（antigravity / chatgpt）。原 Core::AgentCatalog 已随
    // native 业务层移除——业务规则统一在 Worker，这里只剩展示用的键值对。
    static const QList<QPair<QString, QString>> builtinAgents = {
        {QStringLiteral("antigravity"), QStringLiteral("Antigravity IDE")},
        {QStringLiteral("chatgpt"), QStringLiteral("ChatGPT")},
    };
    for (const auto& item : builtinAgents) {
        QAction* action = submenu->addAction(item.second);
        action->setCheckable(true);
        action->setChecked(values.value(item.first).toBool());
        QObject::connect(action, &QAction::toggled, submenu,
                         [context, key = item.first, action](bool on) {
            if (context.controller != nullptr) {
                context.controller->toggleAgentLink(key, on, action);
            }
        });
    }

    // 自定义联动 Agent（config.json 的 agent_link.custom_agents，只读监听）
    const QVariantList customAgents = values.value(QStringLiteral("custom_agents")).toList();
    for (const QVariant& entry : customAgents) {
        const QVariantMap mapped = entry.toMap();
        const QString key = mapped.value(QStringLiteral("key")).toString();
        if (key.isEmpty()) {
            continue;
        }
        QString label = mapped.value(QStringLiteral("name")).toString();
        if (label.isEmpty()) {
            label = key;
        }
        QAction* action = submenu->addAction(label);
        action->setCheckable(true);
        action->setChecked(values.value(key).toBool());
        QObject::connect(action, &QAction::toggled, submenu,
                         [context, key, action](bool on) {
            if (context.controller != nullptr) {
                context.controller->toggleAgentLink(key, on, action);
            }
        });
    }

    submenu->addSeparator();
    struct OptionSpec {
        const char* key;
        const char* label;
        bool defaultValue;
    };
    static const OptionSpec kOptions[] = {
        {"notify_state", "开始干活气泡提醒", false},
        {"notify_done", "任务完成气泡提醒", true},
        {"notify_activity", "过程汇报气泡（正在读文件/跑命令…）", false},
    };
    for (const OptionSpec& option : kOptions) {
        const QString key = QString::fromLatin1(option.key);
        QAction* action = submenu->addAction(QString::fromUtf8(option.label));
        action->setCheckable(true);
        const QVariant stored = values.value(key);
        action->setChecked(stored.isValid() ? stored.toBool() : option.defaultValue);
        QObject::connect(action, &QAction::toggled, submenu, [context, key](bool on) {
            if (context.controller != nullptr) {
                context.controller->setAgentLinkOption(key, on);
            }
        });
    }
    return submenu;
}

QAction* addQuit(QMenu* menu, const MenuContext& context) {
    return addMenuAction(menu, QString::fromUtf8(u8"退出"), QStringLiteral("exit"),
                         [context]() {
        if (context.controller != nullptr) {
            context.controller->requestQuit();
        }
    }, true);
}

} // namespace Pet::UI

#include "MenuPrimitives.moc"
