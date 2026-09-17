#pragma once

#include <QImage>
#include <QString>

namespace Pet::Media {

// 对应 Python pet/media/animation_thumbnail.py：
// 为菜单里的动画条目解码"代表帧"，带内存缓存 + 磁盘缓存，线程安全。
class AnimationThumbnail {
public:
    // Python REPRESENTATIVE_FRACTION
    static constexpr double kRepresentativeFraction = 0.62;

    // Python representative_frame_index(frame_count, fraction)
    static int representativeFrameIndex(int frameCount,
                                        double fraction = kRepresentativeFraction);

    // Python decode_representative_frame(path)：失败返回空 QImage，不抛异常。
    static QImage decodeRepresentativeFrame(const QString& path);

    // 磁盘缓存目录（与 Python 使用同一命名，便于两端相互复用）。
    static QString diskCacheDir();
};

} // namespace Pet::Media
