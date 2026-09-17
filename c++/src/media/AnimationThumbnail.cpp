#include "AnimationThumbnail.hpp"

#include "infrastructure/PathHelper.hpp"

#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QImageReader>
#include <QMutex>
#include <QMutexLocker>
#include <QProcess>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSemaphore>
#include <QSet>
#include <QWaitCondition>
#include <algorithm>

namespace Pet::Media {

namespace {

constexpr int kMemoryCacheLimit = 128;   // Python _CACHE_LIMIT
constexpr int kDiskCacheLimit = 256;     // Python _DISK_CACHE_LIMIT

QMutex g_mutex;
QWaitCondition g_condition;
QHash<QString, QImage> g_memoryCache;
QSet<QString> g_inflight;
// Python _DECODE_SEMAPHORE = BoundedSemaphore(2)：限制并发解码进程数。
QSemaphore g_decodeSemaphore(2);

QString cacheDirPath() {
    return QDir(QDir::tempPath()).filePath(QStringLiteral("desktop-pet-thumbs"));
}

QString cacheKey(const QFileInfo& info) {
    return info.absoluteFilePath() + QLatin1Char('|')
        + QString::number(info.lastModified().toMSecsSinceEpoch()) + QLatin1Char('|')
        + QString::number(info.size());
}

QString diskCachePath(const QString& key) {
    const QByteArray digest = QCryptographicHash::hash(key.toUtf8(), QCryptographicHash::Sha1).toHex();
    return QDir(cacheDirPath()).filePath(QString::fromLatin1(digest) + QStringLiteral(".png"));
}

QImage readDiskCache(const QString& key) {
    const QImage image(diskCachePath(key));
    return image.isNull() ? QImage() : image;
}

void trimDiskCache() {
    QDir dir(cacheDirPath());
    if (!dir.exists()) {
        return;
    }
    // QDir::Time 为最新在前，删除尾部最旧的条目。
    const QFileInfoList entries = dir.entryInfoList({QStringLiteral("*.png")}, QDir::Files, QDir::Time);
    for (int index = kDiskCacheLimit; index < entries.size(); ++index) {
        QFile::remove(entries.at(index).absoluteFilePath());
    }
}

void writeDiskCache(const QString& key, const QImage& image) {
    QDir().mkpath(cacheDirPath());
    QSaveFile file(diskCachePath(key));
    if (!file.open(QIODevice::WriteOnly)) {
        return;
    }
    if (!image.save(&file, "PNG")) {
        file.cancelWriting();
        return;
    }
    if (file.commit()) {
        trimDiskCache();
    }
}

// 通过 ffmpeg 元数据输出解析时长（秒）；失败返回 0。
double probeDurationSeconds(const QString& path) {
    QProcess process;
    process.start(Infrastructure::PathHelper::getFfmpegPath(),
                  {QStringLiteral("-hide_banner"), QStringLiteral("-i"), path});
    if (!process.waitForStarted(3000)) {
        return 0.0;
    }
    // 仅探测元数据时 ffmpeg 会立即以非零码退出，属预期行为。
    process.waitForFinished(8000);
    const QString text = QString::fromLocal8Bit(process.readAllStandardError());

    static const QRegularExpression pattern(
        QStringLiteral("Duration:\\s*(\\d+):(\\d{2}):(\\d{2})\\.(\\d+)"));
    const QRegularExpressionMatch match = pattern.match(text);
    if (!match.hasMatch()) {
        return 0.0;
    }
    const double hours = match.captured(1).toDouble();
    const double minutes = match.captured(2).toDouble();
    const double seconds = match.captured(3).toDouble();
    const double fraction = (QStringLiteral("0.") + match.captured(4)).toDouble();
    return hours * 3600.0 + minutes * 60.0 + seconds + fraction;
}

QImage decodeWebm(const QString& path) {
    const double duration = probeDurationSeconds(path);
    // Python 逐帧推进到 int((count-1)*0.62)，等价时间点约为时长的 62%。
    const double seekSeconds = duration > 0.0
        ? duration * AnimationThumbnail::kRepresentativeFraction
        : 0.0;

    QProcess process;
    const QStringList arguments = {
        QStringLiteral("-hide_banner"),
        QStringLiteral("-loglevel"), QStringLiteral("error"),
        QStringLiteral("-i"), path,
        QStringLiteral("-ss"), QString::number(seekSeconds, 'f', 3),
        QStringLiteral("-frames:v"), QStringLiteral("1"),
        QStringLiteral("-an"),
        QStringLiteral("-f"), QStringLiteral("image2pipe"),
        QStringLiteral("-vcodec"), QStringLiteral("png"),
        QStringLiteral("-"),
    };
    process.start(Infrastructure::PathHelper::getFfmpegPath(), arguments);
    if (!process.waitForStarted(3000)) {
        return QImage();
    }
    if (!process.waitForFinished(15000)) {
        process.kill();
        process.waitForFinished(1000);
        return QImage();
    }

    const QByteArray payload = process.readAllStandardOutput();
    if (payload.isEmpty()) {
        return QImage();
    }
    QImage image;
    if (!image.loadFromData(payload, "PNG")) {
        return QImage();
    }
    return image;
}

QImage decodeGif(const QString& path) {
    QImageReader reader(path);
    const int count = std::max(1, reader.imageCount());
    const int target = AnimationThumbnail::representativeFrameIndex(count);
    if (target > 0 && !reader.jumpToImage(target)) {
        QImageReader sequential(path);
        QImage image;
        for (int index = 0; index <= target; ++index) {
            image = sequential.read();
            if (image.isNull()) {
                break;
            }
        }
        return image;
    }
    return reader.read();
}

QImage decodeFile(const QString& path) {
    if (path.endsWith(QStringLiteral(".gif"), Qt::CaseInsensitive)) {
        return decodeGif(path);
    }
    return decodeWebm(path);
}

} // namespace

int AnimationThumbnail::representativeFrameIndex(int frameCount, double fraction) {
    const int count = std::max(1, frameCount);
    const int index = static_cast<int>((count - 1) * fraction);
    return std::max(0, std::min(count - 1, index));
}

QString AnimationThumbnail::diskCacheDir() {
    return cacheDirPath();
}

QImage AnimationThumbnail::decodeRepresentativeFrame(const QString& path) {
    const QFileInfo info(path);
    if (!info.isFile()) {
        return QImage();
    }
    const QString key = cacheKey(info);

    {
        QMutexLocker locker(&g_mutex);
        const auto cached = g_memoryCache.constFind(key);
        if (cached != g_memoryCache.constEnd()) {
            return QImage(cached.value());
        }
        const QImage diskCached = readDiskCache(key);
        if (!diskCached.isNull()) {
            g_memoryCache.insert(key, diskCached);
            return diskCached;
        }
        // 另一个线程正在解码同一文件：等待其完成，避免重复启动 ffmpeg。
        while (g_inflight.contains(key)) {
            g_condition.wait(&g_mutex, 20000);
            const auto ready = g_memoryCache.constFind(key);
            if (ready != g_memoryCache.constEnd()) {
                return QImage(ready.value());
            }
            const QImage fromDisk = readDiskCache(key);
            if (!fromDisk.isNull()) {
                g_memoryCache.insert(key, fromDisk);
                return fromDisk;
            }
        }
        g_inflight.insert(key);
    }

    QImage image;
    {
        // 与 Python 相同的并发上限：最多两个解码同时进行。
        g_decodeSemaphore.acquire();
        image = decodeFile(path);
        g_decodeSemaphore.release();
    }

    {
        QMutexLocker locker(&g_mutex);
        g_inflight.remove(key);
        if (!image.isNull()) {
            if (g_memoryCache.size() >= kMemoryCacheLimit && !g_memoryCache.isEmpty()) {
                g_memoryCache.erase(g_memoryCache.begin());
            }
            g_memoryCache.insert(key, image);
        }
        g_condition.wakeAll();
    }

    if (!image.isNull()) {
        writeDiskCache(key, image);
    }
    return image;
}

} // namespace Pet::Media
