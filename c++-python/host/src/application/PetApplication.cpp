#include "PetApplication.hpp"
#include "infrastructure/PathHelper.hpp"
#include "ui/PetFrameItem.hpp"
#include "ui/context_menus/MenuIconProvider.hpp"
#include <QFile>
#include <QJsonArray>
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

    // 混合架构：agent_backend=native（默认）时这里什么都不会发生；
    // 仅显式切到 python 才按需拉起 Worker
    setupPythonBackend();
}

PetApplication::~PetApplication() {
    // 优雅关闭（runtime.shutdown + 2s 预算）；进程退出后 Job Object 兜底回收进程树
    if (m_pythonSupervisor) {
        m_pythonSupervisor->stop();
    }
}

int PetApplication::exec() {
    if (m_startupExitCode != 0) {
        return m_startupExitCode;
    }
    return m_app->exec();
}

// ============================================================================
// 混合架构（C++ 宿主 + Python Worker）
// Worker 是唯一的 Agent 业务实现（custom + antigravity + chatgpt）；
// 宿主只负责快照下发、仲裁与进程生命周期，任何 Worker 故障都只影响建议来源，
// 绝不影响渲染与交互。
// ============================================================================

void PetApplication::setupPythonBackend() {
    connect(m_config.get(), &Infrastructure::ConfigManager::valueChanged, this,
            [this](const QString& key, const QVariant& value) {
                Q_UNUSED(value);
                if (key.startsWith(QStringLiteral("agent_link.")) && m_pythonBackendActive) {
                    // Worker 可经 config.patch_request 修改 agent_link 白名单键；
                    // 任何一侧写入后都要以新 revision 重新下发快照
                    ++m_configRevision;
                    sendConfigSnapshot();
                }
            });
    startPythonBackend();
}

void PetApplication::appendWorkerDiagnostics(const QString& text) {
    // Worker 的 stderr 是唯一诊断出口；宿主把它落进数据目录，方便联调排查。
    // 超过 1MiB 轮转到 .old，避免长跑进程把文件写爆。
    const QString path =
        QDir(Infrastructure::PathHelper::getDataDir()).filePath(QStringLiteral("worker-stderr.log"));
    QFile file(path);
    if (file.exists() && file.size() > 1024 * 1024) {
        QFile::remove(path + QStringLiteral(".old"));
        file.rename(path + QStringLiteral(".old"));
    }
    if (!file.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        return;
    }
    file.write(text.toUtf8());
}

QString PetApplication::resolveWorkerEntry(const QString& configured) const {
    // 显式配置优先；缺省按安装布局探测（与 PathHelper::getAssetsDir 的候选习惯一致）
    QStringList candidates;
    if (!configured.trimmed().isEmpty()) {
        const QFileInfo configuredInfo(configured.trimmed());
        candidates << (configuredInfo.isAbsolute()
                           ? configuredInfo.absoluteFilePath()
                           : QDir(Infrastructure::PathHelper::getAppDir()).filePath(configured.trimmed()));
    }
    const QString appDir = Infrastructure::PathHelper::getAppDir();
    // 打包布局：exe 在 dist 根，worker 在 <dist>/c++-python/worker/src
    candidates << QDir(appDir).filePath(QStringLiteral("c++-python/worker/src/pet_worker/__main__.py"))
               // 开发布局：exe 在 <repo>/c++-python/host/build/bin，worker 在 <repo>/c++-python/worker/src
               << QDir(appDir).filePath(QStringLiteral("../../../worker/src/pet_worker/__main__.py"))
               << QDir(appDir).filePath(QStringLiteral("../worker/src/pet_worker/__main__.py"));
    for (const QString& candidate : candidates) {
        if (QFile::exists(candidate)) {
            return QDir::cleanPath(candidate);
        }
    }
    return {};
}

void PetApplication::startPythonBackend() {
    const QVariantMap worker = m_config->pythonWorkerValues();
    if (!worker.value(QStringLiteral("enabled"), true).toBool()) {
        return;
    }
    const QString entry = resolveWorkerEntry(worker.value(QStringLiteral("worker_entry")).toString());
    if (entry.isEmpty()) {
        // 入口缺失：Agent 联动不可用（桌宠本体不受影响），诊断落盘供排查
        qWarning() << "[Python] 找不到 pet_worker 入口，Agent 联动停用";
        appendWorkerDiagnostics(QStringLiteral("[backend] worker entry missing; agent link disabled\n"));
        return;
    }

    if (!m_pythonBridge) {
        m_pythonBridge = std::make_unique<Services::PythonBridge>(this);
        m_arbiter = std::make_unique<Services::ActionArbiter>(m_controller.get(), this);
        m_pythonSupervisor =
            std::make_unique<Services::PythonServiceSupervisor>(m_pythonBridge.get(), this);

        // 握手完成：刷新动作目录、恢复仲裁，再同步配置与宠物快照（重启后同样走这里）
        connect(m_pythonSupervisor.get(), &Services::PythonServiceSupervisor::ready, this,
                [this](const QStringList&) {
                    m_arbiter->setActionCatalog(m_controller->actionCatalog());
                    m_arbiter->setChannelHealthy(true);
                    sendConfigSnapshot();
                    sendPetSnapshot();
                });
        connect(m_pythonSupervisor.get(), &Services::PythonServiceSupervisor::messageReceived, this,
                &PetApplication::handleWorkerMessage);

        // 能力不足 / 终态：落盘并回退 native
        connect(m_pythonSupervisor.get(), &Services::PythonServiceSupervisor::downgraded, this,
                [this](const QString& reason) {
                    appendWorkerDiagnostics(QStringLiteral("[supervisor] downgraded: %1\n").arg(reason));
                });
        connect(m_pythonSupervisor.get(), &Services::PythonServiceSupervisor::stopped, this,
                [this](const QString& reason) {
                    appendWorkerDiagnostics(QStringLiteral("[supervisor] stopped: %1\n").arg(reason));
                    // Worker 彻底起不来（解释器缺失/重启预算耗尽）：Agent 联动停用，
                    // 桌宠本体保持完整可用
                    if (reason.startsWith(QStringLiteral("restart_budget_exhausted"))
                        || reason.startsWith(QStringLiteral("failed_to_start"))) {
                        m_pythonBackendActive = false;
                    }
                });
        // 仲裁结果回执：request_id 与 behavior.propose 一致
        connect(m_arbiter.get(), &Services::ActionArbiter::resultReady, this,
                [this](const QString& requestId, const QString& status, const QString& reason) {
                    appendWorkerDiagnostics(
                        QStringLiteral("[arbiter] result: %1 %2\n").arg(status, reason));
                    QJsonObject payload;
                    payload.insert(QStringLiteral("status"), status);
                    if (!reason.isEmpty()) {
                        payload.insert(QStringLiteral("reason"), reason);
                    }
                    m_pythonBridge->sendResponse(Services::kMsgBehaviorResult, payload, requestId);
                });

        // 通道故障：仲裁器立刻拒收新建议，待 Supervisor 重启完成后恢复
        connect(m_pythonBridge.get(), &Services::PythonBridge::channelBroken, this, [this](const QString& reason) {
            qWarning() << "[Python] 通道故障:" << reason;
            appendWorkerDiagnostics(QStringLiteral("[bridge] channel broken: %1\n").arg(reason));
            m_arbiter->setChannelHealthy(false);
        });

        // Worker stderr → 诊断文件（<dataDir>/worker-stderr.log）。
        // 协议通道与诊断流严格分离：stderr 永远不会污染 stdout 上的 JSONL。
        connect(m_pythonBridge.get(), &Services::PythonBridge::stderrReceived, this,
                [this](const QString& text) { appendWorkerDiagnostics(text); });
        connect(m_pythonBridge.get(), &Services::PythonBridge::diagnostic, this,
                [this](const QString& message) {
                    appendWorkerDiagnostics(QStringLiteral("[bridge] %1\n").arg(message));
                });

        // 宿主代次与语义事件 → Worker；代次变化同时收紧仲裁器
        connect(m_controller.get(), &UI::PetWindowController::petEvent, this,
                [this](const QString& name, quint64 generation) {
                    m_arbiter->setGeneration(generation);
                    if (name == QStringLiteral("drag_start")) {
                        m_arbiter->setInteractionLocked(true);
                    } else if (name == QStringLiteral("drag_end")) {
                        m_arbiter->setInteractionLocked(false);
                    } else if (name == QStringLiteral("character_changed")) {
                        m_arbiter->setActionCatalog(m_controller->actionCatalog());
                    }
                    if (m_pythonBackendActive && m_pythonSupervisor->isReady()) {
                        QJsonObject payload;
                        payload.insert(QStringLiteral("name"), name);
                        payload.insert(QStringLiteral("generation"), static_cast<double>(generation));
                        m_pythonBridge->sendEvent(Services::kMsgPetEvent, payload);
                        if (name == QStringLiteral("hidden") || name == QStringLiteral("shown")
                            || name == QStringLiteral("character_changed")) {
                            // 快照是兜底：事件之外再用全量同步一次可见性/目录/代次
                            sendPetSnapshot();
                        }
                    }
                });
    }

    QStringList arguments{QStringLiteral("-u"), entry};
    m_pythonSupervisor->configure(worker.value(QStringLiteral("python_path"), QStringLiteral("python")).toString(),
                                  arguments, {QStringLiteral("state"), QStringLiteral("behavior"),
                                              QStringLiteral("custom_agent")});
    // 脚本直启 .../pet_worker/__main__.py 时，包父目录（.../worker/src）必须在
    // PYTHONPATH 上；Worker 已按包安装（父目录名不是 pet_worker）则不动环境
    const QDir packageDir(QFileInfo(entry).absolutePath());
    if (packageDir.dirName() == QStringLiteral("pet_worker")) {
        m_pythonBridge->setEnvironmentPath(QDir::cleanPath(packageDir.absoluteFilePath(QStringLiteral(".."))));
    }
    m_pythonBackendActive = true;
    m_configRevision = 0;
    m_arbiter->setChannelHealthy(false); // 握手完成（ready）后才恢复接单
    m_pythonSupervisor->start();
}

void PetApplication::stopPythonBackend() {
    if (m_pythonSupervisor && m_pythonBackendActive) {
        m_pythonSupervisor->stop();
    }
    if (m_arbiter) {
        m_arbiter->setChannelHealthy(false);
    }
    m_pythonBackendActive = false;
}

void PetApplication::sendConfigSnapshot() {
    if (!m_pythonBridge || !m_pythonSupervisor->isReady()) {
        return;
    }
    // 只读快照：完整下发 agent_link 配置；C++ 是唯一写者，Worker 只能提交带
    // revision 的白名单 patch
    const QVariantMap link = m_config->agentLinkValues();
    QJsonObject agents;

    // 内置 agent（antigravity / chatgpt）：事件文件路径由宿主固定为
    // <configDir>/agent-events/<key>.jsonl，启停语义与 custom 一致（agent_link.<key>）
    const QString configDir = QFileInfo(m_config->filePath()).absolutePath();
    static const QStringList builtins = {
        QStringLiteral("antigravity"), QStringLiteral("chatgpt"),
    };
    for (const QString& key : builtins) {
        if (!link.value(key, false).toBool()) {
            continue;
        }
        QJsonObject entry;
        entry.insert(QStringLiteral("enabled"), true);
        entry.insert(QStringLiteral("kind"), key);
        entry.insert(QStringLiteral("display_name"),
                     key == QStringLiteral("antigravity") ? QStringLiteral("Antigravity")
                                                          : QStringLiteral("ChatGPT"));
        entry.insert(QStringLiteral("events_path"),
                     QDir(configDir).filePath(QStringLiteral("agent-events/") + key
                                              + QStringLiteral(".jsonl")));
        entry.insert(QStringLiteral("thinking_text"), m_config->agentThinkingText(key));
        agents.insert(key, entry);
    }

    const QVariantList customAgents = link.value(QStringLiteral("custom_agents")).toList();
    for (const QVariant& item : customAgents) {
        const QVariantMap agent = item.toMap();
        const QString key = agent.value(QStringLiteral("key")).toString();
        if (key.isEmpty()) {
            continue;
        }
        // 与 native AgentLinkManager::applyConfig 同一启停语义：agent_link.<key> 布尔键
        if (!link.value(key, false).toBool()) {
            continue;
        }
        QJsonObject entry;
        entry.insert(QStringLiteral("enabled"), true);
        entry.insert(QStringLiteral("kind"), QStringLiteral("custom"));
        // custom_agents 条目字段：key / name / path（path 即统一协议 JSONL 事件文件）
        entry.insert(QStringLiteral("display_name"), agent.value(QStringLiteral("name")).toString());
        entry.insert(QStringLiteral("events_path"), agent.value(QStringLiteral("path")).toString());
        entry.insert(QStringLiteral("thinking_text"), m_config->agentThinkingText(key));
        agents.insert(key, entry);
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("revision"), m_configRevision);
    payload.insert(QStringLiteral("agent_backend"), QStringLiteral("python"));
    payload.insert(QStringLiteral("agents"), agents);
    m_pythonBridge->sendEvent(Services::kMsgConfigSnapshot, payload);
}

void PetApplication::sendPetSnapshot() {
    if (!m_pythonBridge || !m_pythonSupervisor->isReady()) {
        return;
    }
    QJsonArray actions;
    const auto catalog = m_controller->actionCatalog();
    for (auto it = catalog.constBegin(); it != catalog.constEnd(); ++it) {
        actions.append(it.key());
    }
    QJsonObject payload;
    payload.insert(QStringLiteral("generation"), static_cast<double>(m_controller->generation()));
    payload.insert(QStringLiteral("character"), m_controller->currentCharacter());
    payload.insert(QStringLiteral("visible"), m_controller->petVisible());
    payload.insert(QStringLiteral("interaction_locked"), m_controller->isDragging());
    payload.insert(QStringLiteral("actions"), actions);
    m_pythonBridge->sendEvent(Services::kMsgPetSnapshot, payload);
}

void PetApplication::handleConfigPatch(const Services::Envelope& envelope) {
    // 白名单键：agent_link 下允许 Worker 调整的提醒开关；其余一律拒绝
    static const QStringList whitelisted = {QStringLiteral("notify_state"), QStringLiteral("notify_done"),
                                            QStringLiteral("notify_activity")};
    const QJsonObject payload = envelope.payload;
    const int expectedRevision = payload.value(QStringLiteral("expected_revision")).toInt();
    QJsonObject patch = payload.value(QStringLiteral("patch")).toObject();

    auto respond = [this, &envelope](const QString& status, const QString& reason, int revision) {
        QJsonObject result;
        result.insert(QStringLiteral("status"), status);
        if (!reason.isEmpty()) {
            result.insert(QStringLiteral("reason"), reason);
        }
        result.insert(QStringLiteral("revision"), revision);
        m_pythonBridge->sendResponse(Services::kMsgConfigPatchResult, result, envelope.requestId);
    };

    if (expectedRevision != m_configRevision) {
        respond(QStringLiteral("rejected"), QStringLiteral("revision_mismatch"), m_configRevision);
        return;
    }
    for (auto it = patch.begin(); it != patch.end(); ++it) {
        if (!whitelisted.contains(it.key())) {
            respond(QStringLiteral("rejected"), QStringLiteral("unsupported_key"), m_configRevision);
            return;
        }
    }
    for (auto it = patch.begin(); it != patch.end(); ++it) {
        m_config->setAgentLinkValue(it.key(), it.value().toVariant());
    }
    ++m_configRevision;
    sendConfigSnapshot();
    respond(QStringLiteral("applied"), QString(), m_configRevision);
}

void PetApplication::handleWorkerMessage(const Services::Envelope& envelope) {
    if (!m_pythonBackendActive) {
        return;
    }
    if (envelope.type == Services::kMsgBehaviorPropose) {
        // 状态展示与动作执行分离：动作一律经仲裁，绝不由状态事件直接驱动
        appendWorkerDiagnostics(QStringLiteral("[arbiter] propose received\n"));
        const QJsonObject payload = envelope.payload;
        Services::BehaviorProposal proposal;
        proposal.requestId = envelope.requestId;
        proposal.actionId = payload.value(QStringLiteral("action_id")).toString();
        proposal.generation = static_cast<quint64>(payload.value(QStringLiteral("generation")).toDouble());
        proposal.configRevision = payload.value(QStringLiteral("config_revision")).toInt();
        proposal.ttlMs = payload.contains(QStringLiteral("ttl_ms"))
                             ? payload.value(QStringLiteral("ttl_ms")).toInt()
                             : Services::kDefaultProposalTtlMs;
        if (payload.contains(QStringLiteral("bubble"))) {
            const QJsonObject bubble = payload.value(QStringLiteral("bubble")).toObject();
            proposal.bubbleText = bubble.value(QStringLiteral("text")).toString();
            proposal.bubbleImportant = bubble.value(QStringLiteral("important")).toBool(false);
            proposal.bubbleDurationMs = bubble.contains(QStringLiteral("duration_ms"))
                                            ? bubble.value(QStringLiteral("duration_ms")).toInt()
                                            : 4500;
        }
        m_arbiter->submit(proposal);
    } else if (envelope.type == Services::kMsgAgentStateChanged) {
        // 只做记录/展示，不触发动画
        const QJsonObject payload = envelope.payload;
        qDebug() << "[Python] agent state:" << payload.value(QStringLiteral("agent")).toString()
                 << payload.value(QStringLiteral("state")).toString();
    } else if (envelope.type == Services::kMsgConfigPatchRequest) {
        handleConfigPatch(envelope);
    } else if (envelope.type == Services::kMsgRuntimePing && envelope.kind == Services::Kind::Request) {
        // Worker 的存活探测：原样回 pong
        m_pythonBridge->sendResponse(Services::kMsgRuntimePong, {}, envelope.requestId);
    }
}

} // namespace Pet::Application
