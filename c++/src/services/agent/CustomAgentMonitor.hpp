#pragma once

#include "BaseAgentMonitor.hpp"

namespace Pet::Services {

// 1:1 移植自 Python CustomAgentMonitor（agent_link.custom_agents 配置驱动）。
//
// 只读监听用户指定路径的统一协议 JSONL 事件文件：不创建目录、不写任何外部位置、
// 无需授权弹窗；文件不存在时静默空转等待，出现后自动开始增量读取
// （backfill 防护会跳过历史内容）。
class CustomAgentMonitor : public BaseAgentMonitor {
    Q_OBJECT
public:
    CustomAgentMonitor(const QString& agentKey, const QString& configDir,
                       const QString& eventsPath, QObject* parent = nullptr);

    void start() override;
};

} // namespace Pet::Services
