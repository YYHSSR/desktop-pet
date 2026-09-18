#include "services/bridge/JobObjectGuard.hpp"

#ifdef Q_OS_WIN

#include <windows.h>

#ifndef JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
#define JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 0x00002000
#endif

namespace Pet::Services {

JobObjectGuard::JobObjectGuard()
{
    m_handle = CreateJobObjectW(nullptr, nullptr);
    if (m_handle == nullptr) {
        return;
    }
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limit{};
    limit.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(m_handle, JobObjectExtendedLimitInformation, &limit, sizeof(limit))) {
        CloseHandle(m_handle);
        m_handle = nullptr;
    }
}

JobObjectGuard::~JobObjectGuard()
{
    if (m_handle != nullptr) {
        // 句柄一关，KILL_ON_JOB_CLOSE 兜住整棵进程树
        CloseHandle(m_handle);
    }
}

bool JobObjectGuard::isValid() const
{
    return m_handle != nullptr;
}

bool JobObjectGuard::attach(quint64 processId)
{
    if (m_handle == nullptr || processId == 0) {
        return false;
    }
    HANDLE process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, FALSE, static_cast<DWORD>(processId));
    if (process == nullptr) {
        return false;
    }
    const bool assigned = AssignProcessToJobObject(m_handle, process) != FALSE;
    CloseHandle(process);
    return assigned;
}

} // namespace Pet::Services

#endif // Q_OS_WIN
