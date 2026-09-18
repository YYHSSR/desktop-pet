#pragma once

#include <QString>
#include <QStringList>

#include <functional>

namespace Pet::Services {

// 桌宠窗口向 Agent 联动暴露的最小能力集。
//
// 采用依赖倒置：AgentLinkManager 位于 services 层，而桌宠窗口位于 ui 层
// （ui 依赖 services）。若 manager 直接依赖具体窗口类型会形成循环，
// 因此这里只声明联动真正需要的那几个动作/查询。
class IAgentLinkHost {
public:
    virtual ~IAgentLinkHost() = default;

    // Python win.cats[folder]：指定分类（idle/turn/move/click/random）下的动画名
    [[nodiscard]] virtual QStringList animationNamesFor(const QString& folder) const = 0;
    // Python request_link_anim：一次性动作播放中不打断，存为待播
    virtual void requestLinkAnim(const QString& animationName) = 0;
    // Python request_link_idle：取消待播联动并让桌宠回到待机
    virtual void requestLinkIdle() = 0;
    // 隐藏桌宠时丢弃待播联动动作（Python pause 内清 _pending_link_anim）
    virtual void clearPendingLinkAnim() = 0;
    // Python win._link_next_provider 注入点：动画播完索取下一个联动动作
    virtual void setLinkNextAnimProvider(std::function<QString()> provider) = 0;
    // Python show_bubble：向桌宠头顶冒泡提示
    virtual void showBubble(const QString& text, int durationMs) = 0;
    // Python win._bubble_busy_until（毫秒）：重要气泡占用截止时刻
    [[nodiscard]] virtual qint64 bubbleBusyUntilMs() const = 0;
    // Python win.isVisible()
    [[nodiscard]] virtual bool petVisible() const = 0;
};

} // namespace Pet::Services
