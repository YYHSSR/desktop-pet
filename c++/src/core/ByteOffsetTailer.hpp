#pragma once

#include <QByteArray>
#include <QString>
#include <QStringList>

namespace Pet::Core {

// 1:1 移植自 Python pet/services/agent_link.py 的 ByteOffsetTailer。
//
// 有界 Byte-Offset 文件增量行读取器：
// - 记录上次读取的 byte offset；
// - 启动时若 offset 为 0 且文件已有内容，执行 backfill 防护（跳到末尾），
//   防止重放历史事件；
// - 文件截断/轮转（当前大小 < offset，或文件身份变化）时安全重置到头部；
// - 单次读取最大字节数有界（默认 64KB），防止大文件卡顿；
// - 跨读取边界的半行缓冲（_partial），绝不把半行当整行解析；
// - 超长行进入丢弃模式，跳到下一个换行再恢复。
class ByteOffsetTailer {
public:
    explicit ByteOffsetTailer(const QString& filePath = {}, int maxChunkBytes = 65536);

    void setFilePath(const QString& filePath);
    [[nodiscard]] const QString& filePath() const noexcept { return m_filePath; }

    void reset();

    // 读取自上次 offset 以来的全部完整新增行；无需读取时返回空列表。
    QStringList readNewLines();

private:
    QString m_filePath;
    qint64 m_offset = 0;
    int m_maxChunkBytes = 65536;
    bool m_initialBackfillDone = false;
    QByteArray m_partial;            // 跨读取边界的未完成行缓冲
    bool m_discardUntilNewline = false; // 超长行丢弃模式
    bool m_hasFileId = false;
    QString m_fileId;                // Windows: ino+ctime / POSIX: dev+ino
};

} // namespace Pet::Core
