#include "AgentLinkManager.hpp"

#include "AntigravityMonitor.hpp"
#include "CustomAgentMonitor.hpp"

#include "infrastructure/ConfigManager.hpp"

#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QPointer>
#include <QTimer>
#include <QVariantList>

#include <algorithm>

namespace Pet::Services {
namespace {

qint64 nowMs() {
    return QDateTime::currentMSecsSinceEpoch();
}

// Python _BUSY_STATES
const QStringList& busyStates() {
    static const QStringList states = {QStringLiteral("working"), QStringLiteral("thinking")};
    return states;
}

constexpr qint64 kDoneConfirmMs = 800;
constexpr qint64 kDoneCooldownMs = 5000;
constexpr qint64 kActivityMinIntervalMs = 10000;
constexpr qint64 kActivityGlobalMinMs = 8000;
constexpr qint64 kActivitySameLabelMs = 60000;
constexpr qint64 kToolAnimMinIntervalMs = 2000;
constexpr qint64 kBubbleExternalDeltaMs = 500;
constexpr int kBubbleRetryDelayMs = 2500;
constexpr int kBubbleMaxRetries = 4;

bool contains(const QStringList& list, const QString& value) {
    return list.contains(value);
}

} // namespace

const QHash<QString, QString>& AgentLinkManager::builtinAgentNames() {
    static const QHash<QString, QString> names = {
        {QStringLiteral("antigravity"), QStringLiteral("Antigravity IDE")},
        {QStringLiteral("chatgpt"), QStringLiteral("ChatGPT")},
    };
    return names;
}

const QHash<QString, QString>& AgentLinkManager::toolLabels() {
    // 过程汇报：工具名 → 用户可读文案（不展示原始命令/路径）
    static const QHash<QString, QString> labels = {
        {QStringLiteral("read"), QStringLiteral("正在读文件")},
        {QStringLiteral("write"), QStringLiteral("正在写文件")},
        {QStringLiteral("edit"), QStringLiteral("正在改代码")},
        {QStringLiteral("notebookedit"), QStringLiteral("正在改代码")},
        {QStringLiteral("bash"), QStringLiteral("正在跑命令")},
        {QStringLiteral("shell"), QStringLiteral("正在跑命令")},
        {QStringLiteral("pwsh"), QStringLiteral("正在跑命令")},
        {QStringLiteral("powershell"), QStringLiteral("正在跑命令")},
        {QStringLiteral("grep"), QStringLiteral("正在搜索")},
        {QStringLiteral("glob"), QStringLiteral("正在搜索")},
        {QStringLiteral("search"), QStringLiteral("正在搜索")},
        {QStringLiteral("memory_search"), QStringLiteral("正在翻记忆")},
        {QStringLiteral("webfetch"), QStringLiteral("正在查网页")},
        {QStringLiteral("websearch"), QStringLiteral("正在查网页")},
        {QStringLiteral("fetch"), QStringLiteral("正在查网页")},
        {QStringLiteral("browser"), QStringLiteral("正在查网页")},
        {QStringLiteral("web_fetch"), QStringLiteral("正在查网页")},
        {QStringLiteral("web_search"), QStringLiteral("正在查网页")},
        {QStringLiteral("read_page"), QStringLiteral("正在读网页")},
        {QStringLiteral("task"), QStringLiteral("正在派活给子代理")},
        {QStringLiteral("todowrite"), QStringLiteral("正在列计划")},
        // Antigravity IDE 工具映射
        {QStringLiteral("run_command"), QStringLiteral("正在跑命令")},
        {QStringLiteral("view_file"), QStringLiteral("正在读文件")},
        {QStringLiteral("replace_file_content"), QStringLiteral("正在改代码")},
        {QStringLiteral("multi_replace_file_content"), QStringLiteral("正在改代码")},
        {QStringLiteral("write_to_file"), QStringLiteral("正在写代码")},
        {QStringLiteral("grep_search"), QStringLiteral("正在搜索")},
        {QStringLiteral("search_web"), QStringLiteral("正在查网页")},
        {QStringLiteral("read_url_content"), QStringLiteral("正在读网页")},
        {QStringLiteral("browser_subagent"), QStringLiteral("正在浏览网页")},
        {QStringLiteral("ask_question"), QStringLiteral("正在准备提问")},
        {QStringLiteral("image_generation"), QStringLiteral("正在画图")},
        {QStringLiteral("image_view"), QStringLiteral("正在看图")},
        {QStringLiteral("exec_command"), QStringLiteral("正在跑命令")},
        {QStringLiteral("apply_patch"), QStringLiteral("正在改代码")},
        {QStringLiteral("list_dir"), QStringLiteral("正在浏览目录")},
    };
    return labels;
}

QString AgentLinkManager::unknownToolLabel() {
    return QStringLiteral("正在调用工具");
}

AgentLinkManager::AgentLinkManager(IAgentLinkHost* host,
                                   Infrastructure::ConfigManager* config,
                                   QObject* parent, double minInterval)
    : QObject(parent)
    , m_host(host)
    , m_config(config)
    , m_minIntervalMs(static_cast<qint64>(std::max(0.0, minInterval) * 1000.0)) {

    m_monitors.insert(QStringLiteral("antigravity"),
                      new AntigravityMonitor(configDir(), this));
    m_monitors.insert(QStringLiteral("chatgpt"), new ChatGptMonitor(configDir(), this));

    // 自定义联动 Agent：配置驱动的只读监视器（key/path 已在 config 清洗时保证
    // 合法唯一）；显示名合并进实例级 m_agentNames，内置表保持仅内置。
    // 注意：运行中新增/修改 custom_agents 需重启桌宠生效。
    m_agentNames = builtinAgentNames();
    if (m_config != nullptr) {
        const QVariantList custom =
            m_config->agentLinkValues().value(QStringLiteral("custom_agents")).toList();
        for (const QVariant& item : custom) {
            const QVariantMap entry = item.toMap();
            const QString key = entry.value(QStringLiteral("key")).toString();
            if (key.isEmpty() || m_monitors.contains(key)) {
                continue;
            }
            m_monitors.insert(key, new CustomAgentMonitor(
                                       key, configDir(),
                                       entry.value(QStringLiteral("path")).toString(), this));
            const QString name = entry.value(QStringLiteral("name")).toString();
            m_agentNames.insert(key, name.isEmpty() ? key : name);
        }
    }

    for (auto it = m_monitors.begin(); it != m_monitors.end(); ++it) {
        QObject* object = it.value()->asObject();
        // 两个具体类声明了签名一致的信号，用字符串连接可统一处理
        connect(object, SIGNAL(stateChanged(QString, QString)),
                this, SLOT(onAgentState(QString, QString)));
        connect(object, SIGNAL(activity(QString, QString)),
                this, SLOT(onAgentActivity(QString, QString)));
        if (auto* chatgpt = dynamic_cast<ChatGptMonitor*>(it.value())) {
            connect(chatgpt, &ChatGptMonitor::taskChanged,
                    this, &AgentLinkManager::resetChatGptTask);
            connect(chatgpt, &ChatGptMonitor::snapshotChanged,
                    this, &AgentLinkManager::onChatGptSnapshot);
        }
    }

    // 联动动作链：一次性动作播完后若仍有 Agent 在忙，由窗口回调取下一个动作
    if (m_host != nullptr) {
        const QPointer<AgentLinkManager> guard(this);
        m_host->setLinkNextAnimProvider([guard]() -> QString {
            return guard.isNull() ? QString() : guard->nextBusyAnim();
        });
    }

    applyConfig();
}

AgentLinkManager::~AgentLinkManager() = default;

QString AgentLinkManager::configDir() const {
    return m_config != nullptr ? QFileInfo(m_config->filePath()).absolutePath()
                               : QString();
}

QVariantMap AgentLinkManager::agentConfig() const {
    return m_config != nullptr ? m_config->agentLinkValues() : QVariantMap();
}

QStringList AgentLinkManager::agentKeys() const {
    return m_monitors.keys();
}

QString AgentLinkManager::agentDisplayName(const QString& agentKey) const {
    return m_agentNames.value(agentKey, agentKey);
}

QStringList AgentLinkManager::actNames() const {
    return m_host != nullptr ? m_host->animationNamesFor(QStringLiteral("random"))
                             : QStringList();
}

bool AgentLinkManager::anyBusy() const {
    for (auto it = m_lastRaw.begin(); it != m_lastRaw.end(); ++it) {
        if (contains(busyStates(), it.value())) {
            return true;
        }
    }
    return false;
}

void AgentLinkManager::applyConfig() {
    // 注意用 started()（生命周期状态）而非 isRunning()（会被 pause 置 False）——
    // 否则"隐藏期间关配置"不会真正 stop，恢复显示时又会被 resume 拉起。
    const QVariantMap agentCfg = agentConfig();
    for (auto it = m_monitors.begin(); it != m_monitors.end(); ++it) {
        const bool shouldRun = agentCfg.value(it.key()).toBool();
        if (shouldRun && !it.value()->started()) {
            it.value()->start();
        } else if (!shouldRun && it.value()->started()) {
            it.value()->stop();
        }
    }
}

void AgentLinkManager::stop() {
    for (auto it = m_monitors.begin(); it != m_monitors.end(); ++it) {
        it.value()->stop();
    }
    for (const QString& key : m_donePending) {
        cancelDoneCheck(key);
    }
}

void AgentLinkManager::pause() {
    // 桌宠隐藏时暂停所有监视器，丢弃待播联动动作，并取消所有完成确认计时器
    // （否则隐藏期间计时器到期会在隐藏窗口上切动画/弹气泡）。
    for (auto it = m_monitors.begin(); it != m_monitors.end(); ++it) {
        it.value()->pause();
    }
    if (m_host != nullptr) {
        m_host->clearPendingLinkAnim();
    }
    const QList<QString> pending = m_donePending.values();
    for (const QString& key : pending) {
        cancelDoneCheck(key);
    }
}

void AgentLinkManager::resume() {
    for (auto it = m_monitors.begin(); it != m_monitors.end(); ++it) {
        it.value()->resume();
    }
}

bool AgentLinkManager::setEnabled(const QString& agentKey, bool enabled) {
    if (!m_monitors.contains(agentKey)) {
        return false;
    }

    QString monitorEventsFile;
    bool isCustom = false;
    if (auto* custom = dynamic_cast<CustomAgentMonitor*>(m_monitors.value(agentKey))) {
        monitorEventsFile = custom->eventsFile();
        isCustom = true;
    }
    Q_UNUSED(isCustom);

    if (agentKey == QLatin1String("antigravity")) {
        if (m_config != nullptr) {
            const QString eventsFile = configDir() + QStringLiteral("/agent-events/antigravity.jsonl");
            if (enabled) {
                AntigravityMonitor::installHooks(eventsFile);
            } else {
                AntigravityMonitor::uninstallHooks();
            }
        }
    }
    Q_UNUSED(monitorEventsFile);

    if (m_config != nullptr) {
        m_config->setAgentLinkValue(agentKey, enabled);
    }
    applyConfig();
    if (agentKey == QLatin1String("chatgpt") && !enabled) {
        resetChatGptTask();
    }
    if (enabled) {
        warnIfAgentAbsent(agentKey);
    }
    return true;
}

void AgentLinkManager::warnIfAgentAbsent(const QString& agentKey) {
    // 开启了联动但本机没装对应 Agent 时给用户提示（不然勾了永远没反应）。
    if (m_host == nullptr) {
        return;
    }
    if (auto* custom = dynamic_cast<CustomAgentMonitor*>(m_monitors.value(agentKey))) {
        // 自定义 Agent：事件文件尚未出现时提示路径，避免"勾了没反应"的困惑
        if (QFileInfo::exists(custom->eventsFile())) {
            return;
        }
        m_host->showBubble(
            QString::fromUtf8(u8"已开启 %1 联动监听，但事件文件还没出现——%2 有事件我才能感知到哦")
                .arg(agentDisplayName(agentKey), custom->eventsFile()),
            6000);
        return;
    }

    struct Hint {
        const char* key;
        const char* name;
        const char* marker;
    };
    static const Hint hints[] = {
        {"antigravity", "Antigravity IDE", ".gemini/antigravity-ide"},
    };
    for (const Hint& hint : hints) {
        if (agentKey != QLatin1String(hint.key)) {
            continue;
        }
        const QString marker = QDir::home().filePath(QLatin1String(hint.marker));
        if (!QFileInfo::exists(marker)) {
            m_host->showBubble(
                QString::fromUtf8(u8"已开启 %1 联动监听，但没检测到本机安装 %1——装了它我才能感知到哦")
                    .arg(QLatin1String(hint.name)),
                6000);
        }
        return;
    }
}

bool AgentLinkManager::busyAgentOwnsProcess(const QString& processName,
                                            const QString& title) const {
    const QVariantMap agentCfg = agentConfig();
    const QString process = processName.toLower();
    const QString windowTitle = title.toLower();

    static const QHash<QString, QStringList> processHints = {
        {QStringLiteral("antigravity"),
         {QStringLiteral("antigravity.exe"), QStringLiteral("antigravity-ide.exe")}},
        {QStringLiteral("chatgpt"),
         {QStringLiteral("codex.exe"), QStringLiteral("chatgpt.exe")}},
    };
    for (auto it = processHints.begin(); it != processHints.end(); ++it) {
        if (!process.isEmpty() && it.value().contains(process)
            && agentCfg.value(it.key()).toBool()
            && contains(busyStates(), m_lastRaw.value(it.key()))) {
            return true;
        }
    }

    static const QHash<QString, QStringList> titleHints = {
        {QStringLiteral("antigravity"), {QStringLiteral("antigravity")}},
    };
    for (auto it = titleHints.begin(); it != titleHints.end(); ++it) {
        for (const QString& needle : it.value()) {
            if (!windowTitle.isEmpty() && windowTitle.contains(needle)
                && agentCfg.value(it.key()).toBool()
                && contains(busyStates(), m_lastRaw.value(it.key()))) {
                return true;
            }
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// 联动动作池
// ---------------------------------------------------------------------------

QString AgentLinkManager::nextLinkAnimRotation() {
    // 下一个联动动作：主动作严格交替；每第 3 次插播摸鱼（独立节奏）。
    static const QStringList linkMain = {QString::fromUtf8(u8"写代码"),
                                         QString::fromUtf8(u8"吃Token")};
    static const QStringList linkBreak = {QString::fromUtf8(u8"轻快记录"),
                                          QString::fromUtf8(u8"漂浮踏步")};
    static const QStringList mainKeywords = {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"工作"),
                                             QString::fromUtf8(u8"写"), QString::fromUtf8(u8"打字"),
                                             QString::fromUtf8(u8"敲")};
    static const QStringList breakKeywords = {QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"踏步"),
                                              QString::fromUtf8(u8"伸懒腰")};

    const QStringList acts = actNames();
    QStringList main;
    QStringList brk;
    for (const QString& name : linkMain) {
        if (acts.contains(name)) {
            main.append(name);
        }
    }
    for (const QString& name : linkBreak) {
        if (acts.contains(name)) {
            brk.append(name);
        }
    }

    // 不同角色包的动作名不统一：精确名缺失时按语义关键词回退。
    if (main.isEmpty()) {
        for (const QString& name : acts) {
            for (const QString& keyword : mainKeywords) {
                if (name.contains(keyword)) {
                    main.append(name);
                    break;
                }
            }
        }
    }
    if (brk.isEmpty()) {
        for (const QString& name : acts) {
            for (const QString& keyword : breakKeywords) {
                if (name.contains(keyword)) {
                    brk.append(name);
                    break;
                }
            }
        }
    }
    // 角色包至少有一个动作时，确保 Agent 忙碌期间始终有可见反馈。
    if (main.isEmpty() && brk.isEmpty()) {
        main = acts;
    }
    if (main.isEmpty() && brk.isEmpty()) {
        return QString();
    }

    m_linkSeq += 1;
    if (!brk.isEmpty() && m_linkSeq % 3 == 0) {
        return brk.at((m_linkSeq / 3 - 1) % brk.size());
    }
    if (!main.isEmpty()) {
        return main.at((m_linkSeq - 1) % main.size());
    }
    return brk.at((m_linkSeq - 1) % brk.size());
}

QString AgentLinkManager::nextBusyAnim() {
    // 窗口动画结束回调用：仍有 Agent 在忙 → 下一个联动动作；否则重置轮换计数。
    if (anyBusy()) {
        return nextLinkAnimRotation();
    }
    m_linkSeq = 0;
    return QString();
}

void AgentLinkManager::animateAgentTool(const QString& agentKey, const QString& tool) {
    // 根据 Agent 执行的工具调度桌宠动作（写代码/敲键盘/查阅等）。
    if (m_host == nullptr) {
        return;
    }
    const qint64 now = nowMs();
    const QPair<QString, qint64> last = m_lastToolAnim.value(
        agentKey, QPair<QString, qint64>(QString(), 0));
    if (now - last.second < kToolAnimMinIntervalMs) {
        return;
    }

    const QString key = tool.trimmed().toLower();
    static const QHash<QString, QStringList> hints = {
        {QStringLiteral("bash"), {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"), QString::fromUtf8(u8"工作"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("shell"), {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"), QString::fromUtf8(u8"工作"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("pwsh"), {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"), QString::fromUtf8(u8"工作"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("powershell"), {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"), QString::fromUtf8(u8"工作"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("run_command"), {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"), QString::fromUtf8(u8"工作"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("exec_command"), {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"), QString::fromUtf8(u8"工作"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("edit"), {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("write"), {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("write_to_file"), {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("replace_file_content"), {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("multi_replace_file_content"), {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("apply_patch"), {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"记录"), QString::fromUtf8(u8"打字")}},
        {QStringLiteral("read"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察"), QString::fromUtf8(u8"记录")}},
        {QStringLiteral("view_file"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察"), QString::fromUtf8(u8"记录")}},
        {QStringLiteral("search"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("grep"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("grep_search"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("web_search"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("search_web"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("browser"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("browser_subagent"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"观察")}},
        {QStringLiteral("image_generation"), {QString::fromUtf8(u8"画"), QString::fromUtf8(u8"创作"), QString::fromUtf8(u8"记录")}},
        {QStringLiteral("image_view"), {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"观察")}},
    };

    QStringList words;
    bool hasWords = false;
    if (hints.contains(key)) {
        words = hints.value(key);
        hasWords = true;
    } else {
        static const QStringList writeKeys = {QStringLiteral("write"), QStringLiteral("edit"),
                                              QStringLiteral("patch")};
        static const QStringList execKeys = {QStringLiteral("cmd"), QStringLiteral("exec"),
                                             QStringLiteral("run"), QStringLiteral("bash"),
                                             QStringLiteral("shell")};
        static const QStringList readKeys = {QStringLiteral("read"), QStringLiteral("view"),
                                             QStringLiteral("search"), QStringLiteral("grep"),
                                             QStringLiteral("find")};
        for (const QString& needle : writeKeys) {
            if (key.contains(needle)) {
                words = {QString::fromUtf8(u8"写"), QString::fromUtf8(u8"代码"),
                         QString::fromUtf8(u8"记录")};
                hasWords = true;
                break;
            }
        }
        if (!hasWords) {
            for (const QString& needle : execKeys) {
                if (key.contains(needle)) {
                    words = {QString::fromUtf8(u8"代码"), QString::fromUtf8(u8"敲"),
                             QString::fromUtf8(u8"工作")};
                    hasWords = true;
                    break;
                }
            }
        }
        if (!hasWords) {
            for (const QString& needle : readKeys) {
                if (key.contains(needle)) {
                    words = {QString::fromUtf8(u8"看"), QString::fromUtf8(u8"思考"),
                             QString::fromUtf8(u8"观察")};
                    hasWords = true;
                    break;
                }
            }
        }
    }
    if (!hasWords) {
        return;
    }

    const QStringList acts = actNames();
    QStringList candidates;
    for (const QString& name : acts) {
        for (const QString& word : words) {
            if (name.contains(word)) {
                candidates.append(name);
                break;
            }
        }
    }
    if (candidates.isEmpty()) {
        return;
    }
    m_lastToolAnim.insert(agentKey, {key, now});
    m_linkSeq += 1;
    m_host->requestLinkAnim(candidates.at(m_linkSeq % candidates.size()));
}

// ---------------------------------------------------------------------------
// 状态调度
// ---------------------------------------------------------------------------

void AgentLinkManager::onAgentState(const QString& agentKey, const QString& state) {
    if (m_host == nullptr || !m_host->petVisible()) {
        return;
    }

    const qint64 now = nowMs();

    if (agentKey == QLatin1String("chatgpt")
        && (state == QLatin1String("attention") || state == QLatin1String("error")
            || state == QLatin1String("interrupted") || state == QLatin1String("unavailable"))) {
        const QString previous = m_lastRaw.value(agentKey);
        m_lastRaw.insert(agentKey, state);
        m_lastApplied.insert(agentKey, {state, now});
        cancelDoneCheck(agentKey);
        if (previous == state) {
            return;
        }
        m_host->requestLinkIdle();

        QString message;
        if (state == QLatin1String("attention")) {
            message = QString::fromUtf8(u8"ChatGPT 正在等你确认或回答，去看一眼吧～");
        } else if (state == QLatin1String("error")) {
            message = QString::fromUtf8(u8"ChatGPT 这次任务失败了，去看看错误信息吧。");
        } else if (state == QLatin1String("interrupted")) {
            message = QString::fromUtf8(u8"ChatGPT 任务已中断，我先陪你休息一下。");
        } else {
            message = QString::fromUtf8(u8"暂时读不到 ChatGPT 本机任务状态，正在等待连接。");
        }
        showLinkBubble(message, state == QLatin1String("attention")
                                    || state == QLatin1String("error"));
        return;
    }

    if (agentKey == QLatin1String("chatgpt") && state == QLatin1String("idle")
        && m_lastRaw.value(agentKey) == QLatin1String("attention")) {
        scheduleDoneCheck(agentKey);
    }

    // --- 原始状态流（绕开去抖/节流）：busy→idle 完成检测 ---
    // 不能用 m_lastApplied 判定完成——节流会丢掉紧跟的 idle，导致完成通知丢失。
    const QString prevRaw = m_lastRaw.value(agentKey);
    m_lastRaw.insert(agentKey, state);

    const bool stateBusy = contains(busyStates(), state);
    const bool prevBusy = contains(busyStates(), prevRaw);

    if (stateBusy) {
        cancelDoneCheck(agentKey);
        m_sawAlert.remove(agentKey);
        if (prevRaw != QLatin1String("error")) {
            m_sawError.remove(agentKey);
        }
    } else if ((state == QLatin1String("attention") || state == QLatin1String("error"))
               && prevBusy) {
        m_sawAlert.insert(agentKey);
        if (state == QLatin1String("error")) {
            m_sawError.insert(agentKey);
        }
        // busy 后的 attention/error 进入完成确认（800ms 内回忙则取消）
        scheduleDoneCheck(agentKey);
    } else if ((state == QLatin1String("idle") || state == QLatin1String("sleeping"))
               && prevBusy) {
        // working/thinking → idle：疑似任务完成，800ms 稳定确认
        scheduleDoneCheck(agentKey);
    }

    // 去抖：同一 Agent 连续相同状态只生效第一次
    const auto lastIt = m_lastApplied.constFind(agentKey);
    const bool hasLast = lastIt != m_lastApplied.constEnd();
    if (hasLast && lastIt.value().first == state) {
        return;
    }
    // 节流：同一 Agent 两次动作/气泡切换最小间隔（chatgpt 豁免）
    if (hasLast && (now - lastIt.value().second) < m_minIntervalMs
        && agentKey != QLatin1String("chatgpt")) {
        return;
    }
    m_lastApplied.insert(agentKey, {state, now});

    // 状态 -> 桌宠行为映射
    if (stateBusy) {
        const QString anim = nextLinkAnimRotation();
        if (!anim.isEmpty()) {
            m_host->requestLinkAnim(anim);
        }
        maybeNotifyStart(agentKey, prevRaw, state);
    } else if (state == QLatin1String("attention")) {
        // busy 后的 attention 由完成确认流程接管，避免双气泡
        if (!prevBusy) {
            showLinkBubble(QString::fromUtf8(u8"主人，Agent 这边需要你看一眼～"), true);
        }
    } else if (state == QLatin1String("error")) {
        if (!prevBusy) {
            showLinkBubble(QString::fromUtf8(u8"Agent 执行好像遇到报错了…"), true);
        }
    } else if (state == QLatin1String("sleeping") || state == QLatin1String("idle")) {
        m_host->requestLinkIdle();
    }
}

void AgentLinkManager::onAgentActivity(const QString& agentKey, const QString& tool) {
    // 过程汇报气泡（可选，默认关）：白名单工具映射 + 三重限流。
    animateAgentTool(agentKey, tool);

    const QVariantMap agentCfg = agentConfig();
    if (!agentCfg.value(QStringLiteral("notify_activity")).toBool()) {
        return;
    }

    const QString label = toolLabels().value(tool.trimmed().toLower(), unknownToolLabel());
    const qint64 now = nowMs();
    const bool hasLast = m_lastActivity.contains(agentKey);
    const QPair<QString, qint64> last =
        m_lastActivity.value(agentKey, QPair<QString, qint64>(QString(), 0));

    const qint64 minInterval = static_cast<qint64>(
        agentCfg.value(QStringLiteral("activity_interval"),
                       static_cast<double>(kActivityMinIntervalMs) / 1000.0).toDouble() * 1000.0);
    const qint64 globalMin = static_cast<qint64>(
        agentCfg.value(QStringLiteral("activity_global_min"),
                       static_cast<double>(kActivityGlobalMinMs) / 1000.0).toDouble() * 1000.0);
    const qint64 sameLabelInterval = static_cast<qint64>(
        agentCfg.value(QStringLiteral("activity_same_label"),
                       static_cast<double>(kActivitySameLabelMs) / 1000.0).toDouble() * 1000.0);

    if (hasLast) {
        if (last.first == label && now - last.second < sameLabelInterval) {
            return;
        }
        if (now - last.second < minInterval) {
            return;
        }
    }
    if (now - m_activityGlobalLast < globalMin) {
        return;
    }

    m_lastActivity.insert(agentKey, {label, now});
    m_activityGlobalLast = now;

    // 过程气泡允许平滑刷新
    showLinkBubble(QString::fromUtf8(u8"%1 %2…").arg(agentDisplayName(agentKey), label),
                   false, 2600);
}

void AgentLinkManager::resetChatGptTask() {
    const bool wasActive = m_lastRaw.contains(QStringLiteral("chatgpt"));
    cancelDoneCheck(QStringLiteral("chatgpt"));
    m_lastRaw.remove(QStringLiteral("chatgpt"));
    m_lastApplied.remove(QStringLiteral("chatgpt"));
    m_sawAlert.remove(QStringLiteral("chatgpt"));
    m_sawError.remove(QStringLiteral("chatgpt"));
    m_doneCooldown.remove(QStringLiteral("chatgpt"));
    if (wasActive && !anyBusy() && m_host != nullptr) {
        m_host->requestLinkIdle();
    }
}

void AgentLinkManager::onChatGptSnapshot(const CodexSnapshot& snapshot) {
    // 向菜单暴露一个精简状态对象；不保留任何用户消息。
    m_chatgptSnapshot = snapshot;
}

void AgentLinkManager::scheduleDoneCheck(const QString& agentKey) {
    cancelDoneCheck(agentKey);
    QTimer* timer = m_doneTimers.value(agentKey, nullptr);
    if (timer == nullptr) {
        timer = new QTimer(this);
        timer->setSingleShot(true);
        timer->setInterval(static_cast<int>(kDoneConfirmMs));
        connect(timer, &QTimer::timeout, this, [this, agentKey]() { fireDone(agentKey); });
        m_doneTimers.insert(agentKey, timer);
    }
    m_donePending.insert(agentKey);
    timer->start();
}

void AgentLinkManager::cancelDoneCheck(const QString& agentKey) {
    if (!m_donePending.remove(agentKey)) {
        return;
    }
    if (QTimer* timer = m_doneTimers.value(agentKey, nullptr)) {
        timer->stop();
    }
}

void AgentLinkManager::fireDone(const QString& agentKey) {
    // 800ms 稳定确认到期：期间回忙则不算完成；配置/冷却在弹出前再查。
    m_donePending.remove(agentKey);
    if (QTimer* timer = m_doneTimers.value(agentKey, nullptr)) {
        timer->stop();
    }
    if (m_host == nullptr || !m_host->petVisible()) {
        return; // 隐藏中不弹不切（pause 已取消计时器，这里是兜底）
    }
    if (contains(busyStates(), m_lastRaw.value(agentKey))) {
        return;
    }

    const QVariantMap agentCfg = agentConfig();
    if (!agentCfg.value(QStringLiteral("notify_done"), true).toBool()) {
        return;
    }
    const qint64 now = nowMs();
    if (now - m_doneCooldown.value(agentKey, 0) < kDoneCooldownMs) {
        return;
    }
    m_doneCooldown.insert(agentKey, now);

    const QString name = agentDisplayName(agentKey);
    QString text;
    if (m_sawAlert.contains(agentKey)) {
        // busy 期间出现过 attention/error：不暗示"成功完成"
        text = QString::fromUtf8(u8"%1 那边停了，结果怎么样要主人自己看一眼哦～").arg(name);
    } else {
        text = QString::fromUtf8(u8"%1 干完活啦，去看看成果吧～").arg(name);
    }
    m_sawAlert.remove(agentKey);
    m_sawError.remove(agentKey);

    // 仅当没有其他 Agent 仍在忙时恢复（避免 A 完成顶掉 B 的工作动画）。
    bool othersBusy = false;
    for (auto it = m_lastRaw.begin(); it != m_lastRaw.end(); ++it) {
        if (it.key() != agentKey && contains(busyStates(), it.value())) {
            othersBusy = true;
            break;
        }
    }
    if (!othersBusy) {
        m_host->requestLinkIdle();
        m_lastApplied.insert(agentKey, {QStringLiteral("idle"), now});
    }
    showLinkBubble(text, true);
}

void AgentLinkManager::showLinkBubble(const QString& text, bool important, int durationMs,
                                      int retried) {
    // 联动气泡：不顶掉正在占用气泡位的重要气泡（attention 等）。
    // 若是前一条联动气泡自身的占位，则允许平滑实时刷新；
    // 普通气泡遇外部重要气泡直接让路丢弃；外部重要气泡每 2.5s 重试至多 4 次。
    if (m_host == nullptr) {
        return;
    }
    const qint64 now = nowMs();
    const qint64 busyUntil = m_host->bubbleBusyUntilMs();
    const bool externalBusy = (now < busyUntil)
        && (busyUntil > m_linkBubbleBusyUntil + kBubbleExternalDeltaMs);
    if (externalBusy) {
        if (!important || retried >= kBubbleMaxRetries) {
            return;
        }
        const QPointer<AgentLinkManager> guard(this);
        QTimer::singleShot(kBubbleRetryDelayMs, this, [guard, text, durationMs, retried]() {
            if (!guard.isNull()) {
                guard->showLinkBubble(text, true, durationMs, retried + 1);
            }
        });
        return;
    }
    m_host->showBubble(text, durationMs);
    m_linkBubbleBusyUntil = m_host->bubbleBusyUntilMs();
}

QString AgentLinkManager::thinkingText(const QString& agentKey) const {
    // thinking 气泡文案：按 Agent 自定义 > 旧全局自定义 > 按 Agent 默认。
    static const QHash<QString, QString> defaults = {
        {QStringLiteral("antigravity"), QString::fromUtf8(u8"Antigravity 正在深度思考……")},
        {QStringLiteral("chatgpt"), QString::fromUtf8(u8"ChatGPT 正在认真思考，我陪你等～")},
    };

    const QVariantMap agentCfg = agentConfig();
    QString custom;
    const QVariantMap thinkingTexts =
        agentCfg.value(QStringLiteral("thinking_texts")).toMap();
    custom = thinkingTexts.value(agentKey).toString().trimmed();
    if (custom.isEmpty()) {
        // 兼容旧的全局 thinking_text 字段（设置页保存时已自动迁移）
        custom = agentCfg.value(QStringLiteral("thinking_text")).toString().trimmed();
    }
    const QString name = agentDisplayName(agentKey);
    if (!custom.isEmpty()) {
        return custom.replace(QStringLiteral("{name}"), name);
    }
    if (defaults.contains(agentKey)) {
        return defaults.value(agentKey);
    }
    return QString::fromUtf8(u8"%1 正在深度思考……").arg(name);
}

void AgentLinkManager::maybeNotifyStart(const QString& agentKey, const QString& previousRaw,
                                        const QString& state) {
    // 开始干活气泡：仅「非 busy → busy」时提示（thinking↔working 互跳不弹）。
    const QVariantMap agentCfg = agentConfig();
    if (!agentCfg.value(QStringLiteral("notify_state")).toBool()) {
        return;
    }
    if (contains(busyStates(), previousRaw)) {
        return;
    }
    const QString name = agentDisplayName(agentKey);
    if (state == QLatin1String("thinking")) {
        showLinkBubble(thinkingText(agentKey), false, 3200);
    } else {
        showLinkBubble(QString::fromUtf8(u8"%1 开始干活啦～随时为你效劳！").arg(name), false, 3200);
    }
}

} // namespace Pet::Services
