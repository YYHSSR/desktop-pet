#include "MusicDetector.hpp"

#include <QtGlobal>

#ifdef Q_OS_WIN

#include <windows.h>
#include <mmdeviceapi.h>
#include <endpointvolume.h>
#include <objbase.h>

namespace Pet::Media {

namespace {
// Python MUSIC_PEAK_THRESHOLD：0.0=静音，1.0=满幅，低于该值视为未播放。
constexpr float kPeakThreshold = 0.02f;

// MinGW 的 endpointvolume.h 只有 IAudioMeterInformation 的前置声明，
// 这里给出与 Windows SDK 二进制布局（vtable 顺序）一致的本地定义。
struct ILocalAudioMeter : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetPeakValue(float* peak) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetMeteringChannelCount(UINT* channelCount) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetChannelsPeakValues(UINT32 channelCount, float* peaks) = 0;
    virtual HRESULT STDMETHODCALLTYPE QueryHardwareSupport(DWORD* hardwareSupport) = 0;
};
// IID_IAudioMeterInformation（Windows SDK 文档值）
const IID kAudioMeterIID = {
    0xC02216F6, 0x8C67, 0x4B5B, {0x9D, 0x00, 0xD0, 0x08, 0x73, 0xE3, 0x96, 0x12}};
// MinGW 的 libuuid 未提供这三个 GUID 的定义，这里按 SDK 文档值本地定义
const CLSID kMMDeviceEnumeratorCLSID = {
    0xBCDE0395, 0xE52F, 0x467C, {0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E}};
const IID kIMMDeviceEnumeratorIID = {
    0xA95664D2, 0x9614, 0x4F35, {0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6}};
}

MusicDetector::~MusicDetector() {
    if (m_meter != nullptr) {
        static_cast<ILocalAudioMeter*>(m_meter)->Release();
        m_meter = nullptr;
    }
    if (m_device != nullptr) {
        static_cast<IMMDevice*>(m_device)->Release();
        m_device = nullptr;
    }
    if (m_enumerator != nullptr) {
        static_cast<IMMDeviceEnumerator*>(m_enumerator)->Release();
        m_enumerator = nullptr;
    }
    // 与 CoInitializeEx 配对的释放：仅在本次调用真正初始化成功时执行。
    if (m_comInitialized) {
        CoUninitialize();
        m_comInitialized = false;
    }
}

bool MusicDetector::ensureMeter() {
    if (m_meter != nullptr) {
        return true;
    }

    const HRESULT coInit = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (coInit == S_OK || coInit == S_FALSE) {
        m_comInitialized = true;
    } else if (coInit != RPC_E_CHANGED_MODE) {
        return false; // COM 初始化失败
    }
    // RPC_E_CHANGED_MODE：线程已以其他模式初始化，直接复用即可。

    IMMDeviceEnumerator** enumerator =
        reinterpret_cast<IMMDeviceEnumerator**>(&m_enumerator);
    if (m_enumerator == nullptr) {
        if (FAILED(CoCreateInstance(kMMDeviceEnumeratorCLSID, nullptr, CLSCTX_ALL,
                                    kIMMDeviceEnumeratorIID,
                                    reinterpret_cast<void**>(enumerator)))) {
            return false;
        }
    }

    IMMDevice** device = reinterpret_cast<IMMDevice**>(&m_device);
    if (m_device == nullptr) {
        if (FAILED(static_cast<IMMDeviceEnumerator*>(m_enumerator)
                       ->GetDefaultAudioEndpoint(eRender, eMultimedia, device))) {
            return false;
        }
    }

    ILocalAudioMeter** meter = reinterpret_cast<ILocalAudioMeter**>(&m_meter);
    if (FAILED(static_cast<IMMDevice*>(m_device)
                   ->Activate(kAudioMeterIID, CLSCTX_ALL, nullptr,
                              reinterpret_cast<void**>(meter)))) {
        return false;
    }
    return true;
}

bool MusicDetector::isMusicPlaying() {
    if (!ensureMeter()) {
        return false;
    }
    float peak = 0.0f;
    if (FAILED(static_cast<ILocalAudioMeter*>(m_meter)->GetPeakValue(&peak))) {
        // 设备可能已被移除：丢弃句柄，下次调用重新建立。
        static_cast<ILocalAudioMeter*>(m_meter)->Release();
        m_meter = nullptr;
        return false;
    }
    return peak > kPeakThreshold;
}

} // namespace Pet::Media

#else // !Q_OS_WIN

namespace Pet::Media {

MusicDetector::~MusicDetector() = default;

bool MusicDetector::isMusicPlaying() {
    return false;
}

} // namespace Pet::Media

#endif
