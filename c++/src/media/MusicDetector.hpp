#pragma once

namespace Pet::Media {

// Python music_detect.py 的 C++ 对应实现：
// 通过 WASAPI IAudioMeterInformation 读取系统默认输出设备的音频峰值，
// 判定「正在播放音乐」（峰值 > 0.02，过滤极低电平/数字静音）。
// COM 对象为进程内单例并常驻复用——每次重新 Activate 会累积句柄，
// 长时间运行会崩溃（与 Python 端同样处理）。
class MusicDetector {
public:
    MusicDetector() = default;
    ~MusicDetector();
    MusicDetector(const MusicDetector&) = delete;
    MusicDetector& operator=(const MusicDetector&) = delete;

    // 任何失败（无设备/COM 错误/非 Windows）一律按「未在播放」处理。
    bool isMusicPlaying();

private:
    bool ensureMeter();

    void* m_enumerator = nullptr; // IMMDeviceEnumerator*
    void* m_device = nullptr;     // IMMDevice*
    void* m_meter = nullptr;      // IAudioMeterInformation*
    bool m_comInitialized = false;
};

} // namespace Pet::Media
