#pragma once

#include <QString>

namespace Pet::Infrastructure {

class PathHelper {
public:
    static QString getAppDir();
    static QString getAssetsDir();
    static QString getDataDir();
    static QString getConfigPath();
    // 解析配置中的（可能相对的）素材路径；对应 Python resolve_fun_asset。
    static QString resolveConfiguredAsset(const QString& configured, const QString& fallback);
    static QString getCharactersDir();
    static QString getFfmpegPath();
};

} // namespace Pet::Infrastructure
