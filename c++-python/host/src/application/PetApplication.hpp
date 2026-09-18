#pragma once

#include "ui/PetWindowController.hpp"
#include "ui/SystemTrayBridge.hpp"
#include "infrastructure/ConfigManager.hpp"
#include "services/behavior/ActionArbiter.hpp"
#include "services/bridge/PythonServiceSupervisor.hpp"
#include <QApplication>
#include <QQmlApplicationEngine>
#include <memory>

namespace Pet::Application {

class PetApplication : public QObject {
    Q_OBJECT
public:
    explicit PetApplication(int& argc, char** argv);
    ~PetApplication() override;

    int exec();

private:
    // ---- 混合架构（C++ 宿主 + Python Worker）----
    // 按 agent_backend 决定是否拉起 Worker；native（默认）下不创建任何子进程。
    void setupPythonBackend();
    void startPythonBackend();
    void stopPythonBackend();
    void handleWorkerMessage(const Services::Envelope& envelope);
    void handleConfigPatch(const Services::Envelope& envelope);
    void sendConfigSnapshot();
    void sendPetSnapshot();
    [[nodiscard]] QString resolveWorkerEntry(const QString& configured) const;
    // Worker 诊断流落盘（<dataDir>/worker-stderr.log，超 1MiB 轮转到 .old）
    void appendWorkerDiagnostics(const QString& text);

    std::unique_ptr<QApplication> m_app;
    std::unique_ptr<QQmlApplicationEngine> m_engine;
    std::unique_ptr<Infrastructure::ConfigManager> m_config;
    std::unique_ptr<UI::PetWindowController> m_controller;
    std::unique_ptr<UI::SystemTrayBridge> m_tray;
    int m_startupExitCode = 0;

    std::unique_ptr<Services::PythonBridge> m_pythonBridge;
    std::unique_ptr<Services::PythonServiceSupervisor> m_pythonSupervisor;
    std::unique_ptr<Services::ActionArbiter> m_arbiter;
    bool m_pythonBackendActive = false;
    int m_configRevision = 0;
};

} // namespace Pet::Application
