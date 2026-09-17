#pragma once

#include <QString>

namespace Pet::Services {

// 1:1 移植自 Python pet/infrastructure/codex_status.py。
//
// 面向本机 Codex 历史投影的只读适配器：这是版本容忍的本地适配层，不是公开
// OpenAI API。不支持的 schema 一律返回 unavailable。只有生命周期与工具元数据
// 会离开该模块（不读取对话正文 / 命令参数 / 工具输出 / 推理内容）。
struct CodexSnapshot {
    QString state = QStringLiteral("unavailable");
    QString threadId;
    QString turnId;
    QString tool;
    QString itemId;
    qint64 startedAt = 0;
    QString detail = QStringLiteral("未检测到可读取的本机 Codex 任务");

    bool operator==(const CodexSnapshot& other) const {
        return state == other.state && threadId == other.threadId && turnId == other.turnId
            && tool == other.tool && itemId == other.itemId && startedAt == other.startedAt
            && detail == other.detail;
    }
    bool operator!=(const CodexSnapshot& other) const { return !(*this == other); }
};

class CodexStatusReader {
public:
    explicit CodexStatusReader(const QString& home = {});

    // 有界读取；绝不创建、索引、迁移或写入 Codex 数据库。
    CodexSnapshot read(const QString& threadId = {}) const;

private:
    CodexSnapshot readInternal(const QString& threadId) const;
    // 形如 <home>/<prefix>_<N>.sqlite，取 N 最大者。
    QString databasePath(const QString& prefix) const;

    QString m_home;
};

} // namespace Pet::Services
