#include "PathHelper.hpp"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>

namespace Pet::Infrastructure {

QString PathHelper::getAppDir() {
    return QCoreApplication::applicationDirPath();
}

QString PathHelper::getAssetsDir() {
    QDir dir(getAppDir());
    const QString overridePath = qEnvironmentVariable("DESKTOP_PET_ASSETS").trimmed();
    if (!overridePath.isEmpty() && QFileInfo(overridePath).isDir()) {
        return QDir::cleanPath(QFileInfo(overridePath).absoluteFilePath());
    }
    // 打包目录与源码构建目录。本版本必须自包含：素材一律取本版本自己的
    // assets，绝不回退到其它版本（如 Python 版）的目录——用户单独下载
    // 任何一个版本都应可正常使用。
    const QStringList candidates = {
        dir.filePath("assets"),
        dir.filePath("../assets"),
        dir.filePath("../../assets"),
        dir.filePath("../../../assets")
    };
    for (const QString& candidate : candidates) {
        QFileInfo info(candidate);
        if (info.isDir()) {
            return QDir::cleanPath(info.absoluteFilePath());
        }
    }
    return QDir::cleanPath(candidates.front());
}

QString PathHelper::getDataDir() {
    // 联调/迁移实验的隔离数据目录（先例：DESKTOP_PET_ASSETS）。
    // 混合架构下 Worker 由宿主拉起并只读同一份 config.json，
    // 实验时必须避免与旧版 Python 进程同时写同一份数据目录。
    const QString overridePath = qEnvironmentVariable("DESKTOP_PET_DATA").trimmed();
    if (!overridePath.isEmpty()) {
        QDir().mkpath(overridePath);
        return QDir::cleanPath(QFileInfo(overridePath).absoluteFilePath());
    }
#ifdef Q_OS_WIN
    const QString base = qEnvironmentVariable("APPDATA",
        QStandardPaths::writableLocation(QStandardPaths::AppDataLocation));
    const QString dataDir = QDir(base).filePath("desktop-pet");
#else
    const QString dataDir = QDir(QStandardPaths::writableLocation(
        QStandardPaths::GenericConfigLocation)).filePath("desktop-pet");
#endif
    QDir().mkpath(dataDir);
    return QDir::cleanPath(dataDir);
}

QString PathHelper::getConfigPath() {
    return QDir(getDataDir()).filePath(QStringLiteral("config.json"));
}

QString PathHelper::resolveConfiguredAsset(const QString& configured, const QString& fallback) {
    // 对应 Python fun_image_popup.resolve_fun_asset：
    // 空值回退默认；绝对路径存在则用之；相对路径按应用资源根解析。
    const QString candidate = configured.trimmed();
    if (candidate.isEmpty()) {
        return QDir::cleanPath(fallback);
    }

    const QFileInfo info(candidate);
    if (info.isAbsolute()) {
        return info.exists() ? QDir::cleanPath(info.absoluteFilePath()) : QDir::cleanPath(fallback);
    }

    // 相对路径基准是 assets 的父目录（Python resource_root()）。
    const QString base = QDir(getAssetsDir()).filePath(QStringLiteral(".."));
    const QString bundled = QDir::cleanPath(QDir(base).filePath(candidate));
    return QFileInfo::exists(bundled) ? bundled : QDir::cleanPath(fallback);
}

QString PathHelper::getCharactersDir() {
    return QDir(getAssetsDir()).filePath("characters");
}

QString PathHelper::getFfmpegPath() {
    // WebM 帧解码的外部依赖。候选顺序：exe 旁 → 上一级 → 本版本 assets
    // （随版本分发，保证独立下载可用）→ PATH。任何一个版本都不依赖
    // 其它版本目录里的 ffmpeg。
    const QStringList candidates = {
        QDir(getAppDir()).filePath("ffmpeg.exe"),
        QDir(getAppDir()).filePath("../ffmpeg.exe"),
        QDir(getAssetsDir()).filePath("ffmpeg.exe"),
    };
    for (const QString& candidate : candidates) {
        if (QFile::exists(candidate)) {
            return QDir::cleanPath(candidate);
        }
    }
    return "ffmpeg";
}

} // namespace Pet::Infrastructure
