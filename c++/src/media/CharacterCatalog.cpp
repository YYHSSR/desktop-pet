#include "CharacterCatalog.hpp"
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>

namespace Pet::Media {

CharacterCatalog::CharacterCatalog(QObject* parent)
    : QObject(parent) {}

void CharacterCatalog::scanCharacters(const QString& charactersRoot) {
    m_charactersRoot = charactersRoot;
    m_characters.clear();

    QDir rootDir(charactersRoot);
    if (rootDir.exists()) {
        // Python 使用 sorted(root.iterdir())，这里显式按名称排序，避免依赖文件系统枚举顺序。
        QStringList entries = rootDir.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
        for (const QString& entry : entries) {
            m_characters.append(entry);
        }
    }
    emit catalogUpdated();
}

QStringList CharacterCatalog::availableCharacters() const {
    return m_characters;
}

QString CharacterCatalog::resolveAnimationPath(const QString& character, PetActionState action) const {
    QString folder;
    switch (action) {
    case PetActionState::Idle:      folder = "idle"; break;
    case PetActionState::Walk:      folder = "move"; break;
    case PetActionState::Fall:      folder = "drag"; break;
    case PetActionState::Hit:       folder = "click"; break;
    case PetActionState::Drag:      folder = "drag"; break;
    case PetActionState::Drop:      folder = "idle"; break;
    case PetActionState::Work:      folder = "random"; break;
    case PetActionState::Celebrate: folder = "events"; break;
    }

    QString charName = character;
    QDir baseDir(QDir(m_charactersRoot).filePath(charName));
    if (!baseDir.exists() && !m_characters.isEmpty()) {
        charName = m_characters.first();
        baseDir = QDir(QDir(m_charactersRoot).filePath(charName));
    }
    if (!baseDir.exists()) {
        return "";
    }

    // 优先检查 baseDir/videos/<folder> 或 baseDir/<folder>
    QStringList candidates = {
        baseDir.filePath("videos/" + folder),
        baseDir.filePath(folder),
        baseDir.filePath("videos"),
        baseDir.absolutePath()
    };

    for (const QString& candidatePath : candidates) {
        QDir dir(candidatePath);
        if (dir.exists()) {
            QStringList files = dir.entryList(QStringList() << "*.webm" << "*.gif", QDir::Files);
            if (!files.isEmpty()) {
                return dir.filePath(files.first());
            }
        }
    }

    // 兜底递归扫描整个角色目录
    QDirIterator it(baseDir.absolutePath(), QStringList() << "*.webm" << "*.gif", QDir::Files, QDirIterator::Subdirectories);
    if (it.hasNext()) {
        return it.next();
    }

    return "";
}

QString CharacterCatalog::resolveAnimationPath(const QString& character, const QString& animName) const {
    QDir baseDir(QDir(m_charactersRoot).filePath(character));
    if (!baseDir.exists()) {
        return "";
    }

    QDirIterator it(baseDir.absolutePath(), QStringList() << "*" + animName + "*.webm" << "*" + animName + "*.gif", QDir::Files, QDirIterator::Subdirectories);
    if (it.hasNext()) {
        return it.next();
    }

    return resolveAnimationPath(character, PetActionState::Idle);
}

QMap<QString, QStringList> CharacterCatalog::getAnimationCategories(const QString& character) const {
    QMap<QString, QStringList> result;
    QDir charDir(QDir(m_charactersRoot).filePath(character));
    if (!charDir.exists()) {
        return result;
    }

    struct CategoryMeta {
        QString folder;
        QString label;
    };
    QList<CategoryMeta> cats = {
        {"idle", QStringLiteral("待机")},
        {"turn", QStringLiteral("转向")},
        {"move", QStringLiteral("移动")},
        {"click", QStringLiteral("点击回应")},
        {"random", QStringLiteral("随机动作")}
    };

    for (const auto& cat : cats) {
        QStringList paths;
        QStringList candidateDirs = {
            charDir.filePath("videos/" + cat.folder),
            charDir.filePath(cat.folder)
        };
        for (const QString& d : candidateDirs) {
            QDir dir(d);
            if (dir.exists()) {
                QStringList files = dir.entryList({"*.webm", "*.gif"}, QDir::Files, QDir::Name);
                for (const QString& f : files) {
                    paths.append(dir.filePath(f));
                }
            }
        }
        if (!paths.isEmpty()) {
            result.insert(cat.label, paths);
        }
    }
    return result;
}

QList<AnimationCategory> CharacterCatalog::animationCategoriesOrdered(const QString& character) const {
    QList<AnimationCategory> result;
    QDir charDir(QDir(m_charactersRoot).filePath(character));
    if (!charDir.exists()) {
        return result;
    }

    // 与 Python catalog.build_categories 的 folder 模式一致：
    // 优先 videos/<folder>，不存在时回退 <folder>；空分类跳过。
    struct CategoryMeta {
        const char* folder;
        const char* label;
    };
    static const CategoryMeta categories[] = {
        {"idle", "待机"},
        {"turn", "转向"},
        {"move", "移动"},
        {"click", "点击回应"},
        {"random", "随机动作"},
    };

    for (const CategoryMeta& meta : categories) {
        QStringList paths;
        const QStringList candidateDirs = {
            charDir.filePath(QStringLiteral("videos/%1").arg(QLatin1String(meta.folder))),
            charDir.filePath(QLatin1String(meta.folder)),
        };
        for (const QString& candidate : candidateDirs) {
            QDir dir(candidate);
            if (!dir.exists()) {
                continue;
            }
            const QStringList files = dir.entryList({"*.webm", "*.gif"}, QDir::Files, QDir::Name);
            if (files.isEmpty()) {
                continue;
            }
            for (const QString& file : files) {
                paths.append(dir.absoluteFilePath(file));
            }
            // 目录模式下一次分类只取一个来源目录，避免同一动画被收录两次。
            break;
        }
        if (!paths.isEmpty()) {
            AnimationCategory category;
            category.label = QString::fromUtf8(meta.label);
            category.files = paths;
            result.append(category);
        }
    }
    return result;
}

QStringList CharacterCatalog::animationNames(const QString& character, const QString& folder) const {
    // 与 animationCategoriesOrdered 相同的目录解析规则：
    // 优先 videos/<folder>，不存在时回退 <folder>；取文件主干作为动画名。
    QStringList names;
    const QDir charDir(QDir(m_charactersRoot).filePath(character));
    if (!charDir.exists() || folder.isEmpty()) {
        return names;
    }

    const QStringList candidateDirs = {
        charDir.filePath(QStringLiteral("videos/%1").arg(folder)),
        charDir.filePath(folder),
    };
    for (const QString& candidate : candidateDirs) {
        QDir dir(candidate);
        if (!dir.exists()) {
            continue;
        }
        const QStringList files = dir.entryList({"*.webm", "*.gif"}, QDir::Files, QDir::Name);
        if (files.isEmpty()) {
            continue;
        }
        for (const QString& file : files) {
            const QString name = QFileInfo(file).completeBaseName();
            if (!name.isEmpty() && !names.contains(name)) {
                names.append(name);
            }
        }
        // 目录模式下一次分类只取一个来源目录，避免同一动画被收录两次。
        break;
    }
    return names;
}

QString CharacterCatalog::characterDisplayName(const QString& character) const {
    // Python load_character_manifest 的查找优先级：
    // 1) <角色目录>/videos/manifest.json  2) <角色目录>/manifest.json
    const QDir charDir(QDir(m_charactersRoot).filePath(character));
    const QStringList candidates = {
        charDir.filePath(QStringLiteral("videos/manifest.json")),
        charDir.filePath(QStringLiteral("manifest.json")),
    };
    for (const QString& candidate : candidates) {
        QFile file(candidate);
        if (!file.open(QIODevice::ReadOnly)) {
            continue;
        }
        QJsonParseError error;
        const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) {
            continue;
        }
        const QString name = document.object().value(QStringLiteral("name")).toString().trimmed();
        if (!name.isEmpty()) {
            return name;
        }
    }
    return character;
}

} // namespace Pet::Media

