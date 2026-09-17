#include "ByteOffsetTailer.hpp"

#include <QDir>
#include <QFile>
#include <QFileInfo>

#include <algorithm>

#include <sys/stat.h>
#ifdef Q_OS_WIN
#  include <sys/types.h>
#  include <sys/utime.h>
#endif

namespace Pet::Core {
namespace {

// Python 用 st.st_size + 文件身份 (Win: ino,ctime_ns / POSIX: dev,ino)。
// ctime 在 Windows 上是创建时间（追加不变、轮转变化），
// 在 POSIX 上是 inode 变更时间（每次追加都变），因此只能用 dev+ino。
bool statFile(const QString& path, qint64* size, QString* fileId) {
#ifdef Q_OS_WIN
    struct _stat64 info;
    const std::wstring native = QDir::toNativeSeparators(path).toStdWString();
    if (_wstat64(native.c_str(), &info) != 0) {
        return false;
    }
    *size = static_cast<qint64>(info.st_size);
    *fileId = QStringLiteral("%1:%2")
                  .arg(static_cast<qulonglong>(info.st_ino))
                  .arg(static_cast<qlonglong>(info.st_ctime));
#else
    struct stat info;
    const QByteArray native = QFile::encodeName(path);
    if (::stat(native.constData(), &info) != 0) {
        return false;
    }
    *size = static_cast<qint64>(info.st_size);
    *fileId = QStringLiteral("%1:%2")
                  .arg(static_cast<qulonglong>(info.st_dev))
                  .arg(static_cast<qulonglong>(info.st_ino));
#endif
    return true;
}

// 兼容 PowerShell `Add-Content -Encoding UTF8` 在文件首行写入的 BOM。
void stripUtf8Bom(QByteArray* data) {
    static const QByteArray bom("\xEF\xBB\xBF", 3);
    if (data->startsWith(bom)) {
        data->remove(0, bom.size());
    }
}

} // namespace

ByteOffsetTailer::ByteOffsetTailer(const QString& filePath, int maxChunkBytes)
    : m_filePath(filePath)
    , m_maxChunkBytes(std::max(1, maxChunkBytes)) {}

void ByteOffsetTailer::setFilePath(const QString& filePath) {
    if (m_filePath == filePath) {
        return;
    }
    m_filePath = filePath;
    reset();
}

void ByteOffsetTailer::reset() {
    m_offset = 0;
    m_initialBackfillDone = false;
    m_partial.clear();
    m_discardUntilNewline = false;
    m_hasFileId = false;
    m_fileId.clear();
}

QStringList ByteOffsetTailer::readNewLines() {
    if (m_filePath.isEmpty() || !QFileInfo(m_filePath).isFile()) {
        return {};
    }

    qint64 size = 0;
    QString fileId;
    if (!statFile(m_filePath, &size, &fileId)) {
        return {};
    }

    // 启动时的首次初始化：跳至当前末尾（backfill 防护），防止重放历史事件。
    if (!m_initialBackfillDone) {
        m_initialBackfillDone = true;
        m_offset = size;
        m_fileId = fileId;
        m_hasFileId = true;
        m_partial.clear();
        return {};
    }

    // 文件被截断，或被轮换成同路径的新文件（bridge rename 后新文件可能
    // 在下次轮询前就长到不小于旧 offset，只看 size 会永久跳过新文件前部）。
    if (size < m_offset || (m_hasFileId && fileId != m_fileId)) {
        m_offset = 0;
        m_partial.clear();
        m_discardUntilNewline = false; // 旧文件的丢弃状态不得泄漏进新文件
    }
    m_fileId = fileId;
    m_hasFileId = true;

    if (size == m_offset) {
        return {};
    }

    const qint64 bytesToRead = std::min<qint64>(size - m_offset, m_maxChunkBytes);
    QByteArray chunk;
    {
        QFile file(m_filePath);
        if (!file.open(QIODevice::ReadOnly)) {
            return {};
        }
        if (!file.seek(m_offset)) {
            return {};
        }
        chunk = file.read(bytesToRead);
        m_offset = file.pos();
    }

    const bool firstChunkOfFile = (m_offset == bytesToRead);
    chunk = m_partial + chunk;

    // 超长行丢弃模式：上个 chunk 已确认某行超过上限，跳到下一个换行再恢复。
    if (m_discardUntilNewline) {
        const int idx = chunk.indexOf('\n');
        if (idx < 0) {
            return {};
        }
        chunk.remove(0, idx + 1);
        m_discardUntilNewline = false;
    }

    if (!chunk.isEmpty() && !chunk.endsWith('\n')) {
        // 末尾是不完整的半行：留到下次拼接。
        const int idx = chunk.lastIndexOf('\n');
        if (idx < 0) {
            m_partial = chunk;
            chunk.clear();
        } else {
            m_partial = chunk.mid(idx + 1);
            chunk.truncate(idx + 1);
        }
        // 防呆：单行超过上限时进入丢弃模式（跳过该超长行剩余部分，
        // 避免把它的"后半截"误当成一条新事件解析）。
        if (m_partial.size() > m_maxChunkBytes) {
            m_partial.clear();
            m_discardUntilNewline = true;
        }
    } else {
        m_partial.clear();
    }

    if (firstChunkOfFile) {
        stripUtf8Bom(&chunk);
    }

    // utf-8-sig + errors="replace"：非法字节替换为 U+FFFD 而不是丢弃整行。
    const QString text = QString::fromUtf8(chunk);
    QStringList lines;
    const QStringList rawLines = text.split(QLatin1Char('\n'), Qt::KeepEmptyParts);
    lines.reserve(rawLines.size());
    for (const QString& rawLine : rawLines) {
        const QString line = rawLine.trimmed();
        if (!line.isEmpty()) {
            lines.append(line);
        }
    }
    return lines;
}

} // namespace Pet::Core
