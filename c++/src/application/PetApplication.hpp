#pragma once

#include "ui/PetWindowController.hpp"
#include "ui/SystemTrayBridge.hpp"
#include "infrastructure/ConfigManager.hpp"
#include <QApplication>
#include <QQmlApplicationEngine>
#include <memory>

namespace Pet::Application {

class PetApplication : public QObject {
    Q_OBJECT
public:
    explicit PetApplication(int& argc, char** argv);
    ~PetApplication() override = default;

    int exec();

private:
    std::unique_ptr<QApplication> m_app;
    std::unique_ptr<QQmlApplicationEngine> m_engine;
    std::unique_ptr<Infrastructure::ConfigManager> m_config;
    std::unique_ptr<UI::PetWindowController> m_controller;
    std::unique_ptr<UI::SystemTrayBridge> m_tray;
    int m_startupExitCode = 0;
};

} // namespace Pet::Application
