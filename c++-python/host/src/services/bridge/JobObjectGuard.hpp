#pragma once

// Windows Job Object 进程树兜底。
//
// 不能假定父进程崩溃时 QProcess 会自动清理 Worker 及其后代（含它拉起的
// ffmpeg）：把 Worker 进程挂进带 KILL_ON_JOB_CLOSE 的 Job Object，
// 宿主无论怎么退出（包括被任务管理器强杀），句柄关闭时整棵进程树都会被回收。
// 非 Windows 平台提供空实现。

#include <QString>

namespace Pet::Services {

#ifdef Q_OS_WIN

class JobObjectGuard {
public:
    JobObjectGuard();
    ~JobObjectGuard();
    JobObjectGuard(const JobObjectGuard&) = delete;
    JobObjectGuard& operator=(const JobObjectGuard&) = delete;

    // 把进程（及其未来子进程）挂进本 Job。已 attach 过的进程不可重复挂。
    bool attach(quint64 processId);
    [[nodiscard]] bool isValid() const;

private:
    void* m_handle = nullptr; // HANDLE，void* 避免在头文件拉 windows.h
};

#else // 非 Windows：无 Job Object，提供等价签名的空实现

class JobObjectGuard {
public:
    JobObjectGuard() = default;
    ~JobObjectGuard() = default;
    JobObjectGuard(const JobObjectGuard&) = delete;
    JobObjectGuard& operator=(const JobObjectGuard&) = delete;

    bool attach(quint64 processId) { Q_UNUSED(processId); return false; }
    [[nodiscard]] bool isValid() const { return false; }
};

#endif // Q_OS_WIN

} // namespace Pet::Services
