#include "CustomAgentMonitor.hpp"

#include <QDir>
#include <QFileInfo>

namespace Pet::Services {
namespace {

// Python Path(...).expanduser()：展开开头的 ~ 与 ~user
QString expandUser(const QString& path) {
    const QString trimmed = path.trimmed();
    if (!trimmed.startsWith(QLatin1Char('~'))) {
        return trimmed;
    }
    const int separator = trimmed.indexOf(QLatin1Char('/'));
    const QString user = (separator < 0) ? trimmed.mid(1)
                                         : trimmed.mid(1, separator - 1);
    const QString rest = (separator < 0) ? QString() : trimmed.mid(separator + 1);
    if (!user.isEmpty()) {
        // 非当前用户的家目录不解析（与 Pathlib 的宽松行为一致：原样返回）
        return trimmed;
    }
    return rest.isEmpty() ? QDir::homePath() : QDir::home().filePath(rest);
}

} // namespace

CustomAgentMonitor::CustomAgentMonitor(const QString& agentKey, const QString& configDir,
                                       const QString& eventsPath, QObject* parent)
    : BaseAgentMonitor(agentKey, configDir, parent) {
    m_eventsFile = expandUser(eventsPath);
    m_eventsDir = QFileInfo(m_eventsFile).absolutePath();
    m_tailer.setFilePath(m_eventsFile);
}

void CustomAgentMonitor::start() {
    // 覆写基类 start：基类会 mkdir 事件目录，这里只读监听外部文件，
    // 不替用户在任意路径创建目录。
    m_running = true;
    m_paused = false;
    m_tailer.reset();
    if (!m_timer->isActive()) {
        m_timer->start();
    }
}

} // namespace Pet::Services
