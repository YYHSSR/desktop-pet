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
    // 打包目录、源码构建目录，以及与 Python 版本并排放置的开发目录。
    const QStringList candidates = {
        dir.filePath("assets"),
        dir.filePath("../assets"),
        dir.filePath("../../assets"),
        dir.filePath("../../../assets"),
        dir.filePath("../../../python/assets")
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
    QString appDir = getAppDir();
    if (QFile::exists(appDir + "/ffmpeg.exe")) {
        return appDir + "/ffmpeg.exe";
    }
    QDir dir(appDir);
    if (dir.cdUp() && QFile::exists(dir.filePath("ffmpeg.exe"))) {
        return dir.filePath("ffmpeg.exe");
    }
    return "ffmpeg";
}

} // namespace Pet::Infrastructure
