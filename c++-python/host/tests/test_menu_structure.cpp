// 现代右键菜单的结构回归测试：确保菜单顺序、分组、文案与档位
// 与 Python 版 build_modern_menu 保持一致。
#include "infrastructure/ConfigManager.hpp"
#include "ui/ModernContextMenu.hpp"
#include "ui/PetWindowController.hpp"

#include <QAction>
#include <QApplication>
#include <QMenu>
#include <QStringList>
#include <QTemporaryDir>
#include <QTextStream>

namespace {

void dumpMenu(QMenu* menu, int depth, QTextStream& out) {
    for (QAction* action : menu->actions()) {
        const QString indent(depth * 2, QLatin1Char(' '));
        if (action->isSeparator()) {
            out << indent << QStringLiteral("---") << "\n";
            continue;
        }
        QString line = indent + action->text();
        if (action->isCheckable()) {
            line += action->isChecked() ? QStringLiteral(" [x]") : QStringLiteral(" [ ]");
        }
        if (!action->icon().isNull()) {
            line += QStringLiteral(" <icon>");
        }
        out << line << "\n";
        if (QMenu* submenu = action->menu()) {
            dumpMenu(submenu, depth + 1, out);
        }
    }
}

QMenu* submenuOf(QMenu* menu, const QString& text) {
    for (QAction* action : menu->actions()) {
        if (action->text() == text) {
            return action->menu();
        }
    }
    return nullptr;
}

QStringList actionTexts(QMenu* menu) {
    QStringList texts;
    if (menu == nullptr) {
        return texts;
    }
    for (QAction* action : menu->actions()) {
        texts.append(action->isSeparator() ? QStringLiteral("---") : action->text());
    }
    return texts;
}

int fail(QTextStream& err, const QString& message) {
    err << "FAILED: " << message << "\n";
    err.flush();
    return 1;
}

} // namespace

int main(int argc, char** argv) {
    QApplication app(argc, argv);

    QTemporaryDir tempDir;
    const QString configPath = tempDir.filePath(QStringLiteral("config.json"));
    Pet::Infrastructure::ConfigManager config(configPath);
    Pet::UI::PetWindowController controller(&config, nullptr);

    Pet::UI::ModernContextMenu menu(&controller, &config);

    QTextStream out(stdout);
    dumpMenu(&menu, 0, out);
    out.flush();

    QTextStream err(stderr);

    // 顶层顺序严格对齐 Python build_modern_menu。
    const QStringList expectedTop = {
        QString::fromUtf8(u8"厉害了我的鲸"),
        QStringLiteral("---"),
        QString::fromUtf8(u8"播放动画"),
        QString::fromUtf8(u8"切换角色"),
        QStringLiteral("---"),
        QString::fromUtf8(u8"播放速率"),
        QString::fromUtf8(u8"大小"),
        QString::fromUtf8(u8"回到右下角"),
        QString::fromUtf8(u8"隐藏桌宠"),
        QString::fromUtf8(u8"不移动"),
        QString::fromUtf8(u8"窗口置顶"),
        QString::fromUtf8(u8"开机自启"),
        QStringLiteral("---"),
        QString::fromUtf8(u8"启动应用"),
        QString::fromUtf8(u8"快捷网址"),
        QString::fromUtf8(u8"Agent 联动"),
        QStringLiteral("---"),
        QString::fromUtf8(u8"桌宠设置"),
        // 对齐 Python modern 菜单：不含「更新与帮助」链接子菜单
        QStringLiteral("---"),
        QString::fromUtf8(u8"退出"),
    };
    const QStringList actualTop = actionTexts(&menu);
    if (actualTop != expectedTop) {
        return fail(err, QStringLiteral("顶层结构不一致\n  期望: %1\n  实际: %2")
                               .arg(expectedTop.join(QStringLiteral(" | ")),
                                    actualTop.join(QStringLiteral(" | "))));
    }

    // 播放动画：五个固定分类，顺序与 Python shared.py 一致。
    const QStringList expectedAnimations = {
        QString::fromUtf8(u8"待机"),
        QString::fromUtf8(u8"转向"),
        QString::fromUtf8(u8"移动"),
        QString::fromUtf8(u8"点击回应"),
        QString::fromUtf8(u8"随机动作"),
    };
    if (actionTexts(submenuOf(&menu, QString::fromUtf8(u8"播放动画"))) != expectedAnimations) {
        return fail(err, QStringLiteral("播放动画分类不一致"));
    }

    // 切换角色：角色项 + 分隔线 + 重命名当前角色…
    const QStringList characterItems = actionTexts(submenuOf(&menu, QString::fromUtf8(u8"切换角色")));
    if (characterItems.size() < 3
        || characterItems.at(characterItems.size() - 2) != QStringLiteral("---")
        || characterItems.last() != QString::fromUtf8(u8"重命名当前角色…")) {
        return fail(err, QStringLiteral("切换角色缺少重命名入口"));
    }

    // 播放速率：1.0x ~ 2.0x 共 11 档。
    const QStringList speedItems = actionTexts(submenuOf(&menu, QString::fromUtf8(u8"播放速率")));
    if (speedItems.size() != 11
        || speedItems.first() != QStringLiteral("1.0x")
        || speedItems.last() != QStringLiteral("2.0x")) {
        return fail(err, QStringLiteral("播放速率档位不一致: %1")
                               .arg(speedItems.join(QStringLiteral(","))));
    }

    // 大小：320px / 461px / 544px / 640px。
    const QStringList expectedSizes = {
        QStringLiteral("320px"), QStringLiteral("461px"),
        QStringLiteral("544px"), QStringLiteral("640px"),
    };
    if (actionTexts(submenuOf(&menu, QString::fromUtf8(u8"大小"))) != expectedSizes) {
        return fail(err, QStringLiteral("大小档位不一致"));
    }

    // Agent 联动：两个内置 Agent + 分隔线 + 三个提醒开关。
    const QStringList agentItems = actionTexts(submenuOf(&menu, QString::fromUtf8(u8"Agent 联动")));
    if (agentItems.size() != 6
        || agentItems.at(0) != QStringLiteral("Antigravity IDE")
        || agentItems.at(1) != QStringLiteral("ChatGPT")
        || agentItems.at(5) != QString::fromUtf8(u8"过程汇报气泡（正在读文件/跑命令…）")) {
        return fail(err, QStringLiteral("Agent 联动子菜单不一致: %1")
                               .arg(agentItems.join(QStringLiteral(" | "))));
    }

    out << "\nMENU STRUCTURE OK\n";
    out.flush();
    return 0;
}
