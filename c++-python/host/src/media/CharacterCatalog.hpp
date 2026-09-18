#pragma once

#include <QHash>
#include <QList>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QMap>
#include <map>

namespace Pet::Media {

enum class PetActionState {
    Idle,
    Walk,
    Fall,
    Hit,
    Drag,
    Drop,
    Work,
    Celebrate
};

// 一个动画分类：标签 + 已排序的视频文件绝对路径。
// 顺序对应 Python shared.py build_animation_categories 的固定顺序。
struct AnimationCategory {
    QString label;
    QStringList files;
};

class CharacterCatalog : public QObject {
    Q_OBJECT
public:
    explicit CharacterCatalog(QObject* parent = nullptr);

    void scanCharacters(const QString& charactersRoot);
    // 外部角色目录（对齐 Python external_character_dirs）：exe 旁 / 数据目录下的
    // characters/ 也会并入；同名角色以内置优先，不覆盖
    void scanExternalCharacters(const QString& charactersRoot);
    QStringList availableCharacters() const;
    QString resolveAnimationPath(const QString& character, PetActionState action) const;
    QString resolveAnimationPath(const QString& character, const QString& animName) const;
    QMap<QString, QStringList> getAnimationCategories(const QString& character) const;

    // 保序版本：待机 / 转向 / 移动 / 点击回应 / 随机动作，空分类直接跳过。
    // QMap 按 key 排序会打乱中文标签顺序，菜单依赖此接口。
    QList<AnimationCategory> animationCategoriesOrdered(const QString& character) const;

    // 指定分类（idle/turn/move/click/random）下的动画名（文件主干）。
    // 对应 Python catalog 的 cats[...] 名称列表，供 Agent 联动动作池匹配使用。
    QStringList animationNames(const QString& character, const QString& folder) const;

    // 角色显示名：manifest.json 的 name 字段优先，缺省回退目录 id。
    QString characterDisplayName(const QString& character) const;

signals:
    void catalogUpdated();

private:
    QString m_charactersRoot;
    QHash<QString, QString> m_charRoots; // 外部目录注册表：角色名 → 根目录
    [[nodiscard]] QString characterRootFor(const QString& character) const;
    QStringList m_characters;
};

} // namespace Pet::Media
