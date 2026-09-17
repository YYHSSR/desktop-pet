#pragma once

#include <QString>

class QObject;

namespace Pet::Services {

// 统一监视器接口：屏蔽 BaseAgentMonitor 体系与 ChatGPT 适配器的实现差异，
// 让 AgentLinkManager 可以用同一套生命周期代码驱动全部 Agent。
class IAgentMonitor {
public:
    virtual ~IAgentMonitor() = default;

    [[nodiscard]] virtual QString agentKey() const = 0;
    // Python is_running()：生命周期开启且未被 pause
    [[nodiscard]] virtual bool isRunning() const = 0;
    // Python _running：仅生命周期状态
    [[nodiscard]] virtual bool started() const = 0;
    virtual void start() = 0;
    virtual void stop() = 0;
    virtual void pause() = 0;
    virtual void resume() = 0;
    // 用于信号连接与父子关系（两个具体类都是 QObject）
    [[nodiscard]] virtual QObject* asObject() = 0;
};

} // namespace Pet::Services
