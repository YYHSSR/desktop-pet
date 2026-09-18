#include "services/bridge/ProtocolCodec.hpp"

#include <QJsonDocument>
#include <QJsonParseError>
#include <QStringDecoder>

namespace Pet::Services {

namespace {

QString seqKey(const Envelope& envelope)
{
    return envelope.sessionId + QLatin1Char('|') + kindToString(envelope.kind);
}

} // namespace

ProtocolCodec::ProtocolCodec(int maxMessageBytes) : m_maxMessageBytes(maxMessageBytes) {}

int ProtocolCodec::errorTotal() const
{
    int total = 0;
    for (auto it = errorCounts.cbegin(); it != errorCounts.cend(); ++it) {
        total += it.value();
    }
    return total;
}

QList<Envelope> ProtocolCodec::feed(const QByteArray& data)
{
    QList<Envelope> delivered;
    if (aborted || data.isEmpty()) {
        return delivered;
    }
    m_buffer += data;
    while (!aborted) {
        const qsizetype index = m_buffer.indexOf('\n');
        if (index < 0) {
            // 规则 3：没有换行时才用总长度判断——这是「无限累积」的唯一出口。
            if (m_buffer.size() > m_maxMessageBytes) {
                ++errorCounts[kErrPayloadTooLarge];
                m_buffer.clear();
                aborted = true;
            }
            break;
        }

        QByteArray line = m_buffer.left(index);
        m_buffer.remove(0, index + 1);
        if (line.endsWith('\r')) { // 容忍 CRLF
            line.chop(1);
        }
        if (line.isEmpty()) { // 空行跳过，不算错误
            continue;
        }

        // 规则 2：整行长度单独判定，超限只丢这一行。
        if (line.size() > m_maxMessageBytes) {
            ++errorCounts[kErrPayloadTooLarge];
            continue;
        }

        decodeLine(line, delivered);
    }
    return delivered;
}

void ProtocolCodec::finish()
{
    if (aborted) {
        return;
    }
    if (!m_buffer.trimmed().isEmpty()) {
        ++errorCounts[kErrTruncatedOnEof];
    }
    m_buffer.clear();
}

void ProtocolCodec::decodeLine(const QByteArray& line, QList<Envelope>& delivered)
{
    // 整行字节完整后才做 UTF-8 解码；必须显式校验——Qt 的静默替换模式
    // 会把非法字节换成 U+FFFD，非法 UTF-8 就探测不到了。
    QStringDecoder decoder(QStringConverter::Utf8);
    const QString text = decoder(line);
    if (decoder.hasError()) {
        ++errorCounts[kErrInvalidUtf8];
        return;
    }

    QJsonParseError parseError{};
    const QJsonDocument document = QJsonDocument::fromJson(text.toUtf8(), &parseError);
    if (parseError.error != QJsonParseError::NoError) {
        ++errorCounts[kErrInvalidJson];
        return;
    }
    // 顶层是标量/数组：Python json.loads 不报错，由 validate_envelope 拒为
    // invalid_envelope；这里保持同一归类，不能混进 invalid_json。
    if (!document.isObject()) {
        ++errorCounts[kErrInvalidEnvelope];
        return;
    }

    Envelope envelope;
    const DecodeOutcome outcome = validateEnvelope(document.object(), &envelope);
    switch (outcome.error) {
    case EnvelopeError::None:
        break;
    case EnvelopeError::UnknownType:
        // 信封合法但类型不认识：只记录，绝不触发默认动作。
        unsupported.append(outcome.messageType);
        return;
    case EnvelopeError::InvalidEnvelope:
        ++errorCounts[kErrInvalidEnvelope];
        return;
    case EnvelopeError::UnsupportedVersion:
        ++errorCounts[kErrUnsupportedVersion];
        return;
    }

    if (isReplay(envelope)) {
        ++duplicatesDropped;
        return;
    }

    delivered.append(envelope);
}

bool ProtocolCodec::isReplay(const Envelope& envelope)
{
    const QString key = seqKey(envelope);
    const auto it = m_seenSeq.find(key);
    if (it != m_seenSeq.end()) {
        if (envelope.seq <= it.value()) {
            return true;
        }
        it.value() = envelope.seq;
        return false;
    }
    if (m_seenSeq.size() >= kMaxTrackedSessions) {
        const QString evicted = m_seenSeqOrder.takeFirst();
        m_seenSeq.remove(evicted);
    }
    m_seenSeq.insert(key, envelope.seq);
    m_seenSeqOrder.append(key);
    return false;
}

} // namespace Pet::Services
