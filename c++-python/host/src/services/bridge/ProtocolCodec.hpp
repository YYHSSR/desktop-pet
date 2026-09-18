#pragma once

#include "services/bridge/ProtocolMessage.hpp"

#include <QByteArray>
#include <QHash>
#include <QList>
#include <QStringList>

namespace Pet::Services {

// 增量字节流分帧器：字节 → 合法信封。
//
// 与 pet_worker.transport.Framer 得到完全一致的结果（契约：
// c++-python/protocol/fixtures/framing_cases.json）。只做「字节 → 合法信封」
// 这一件事，不关心业务。所有异常都被收敛成计数字段，除「未终止且超限」之外
// 都不中止会话——一条坏行不该带走整个 Worker。
//
// 分帧判定顺序（顺序错了，超长行的两种用例会得到相反结果）：
// 1. 先在缓冲区里找 \n；
// 2. 找到：取出该整行，**单独**校验这一行的长度，超限记 payload_too_large
//    并丢弃该行，不中止会话；
// 3. 没找到：此时才用**缓冲区总长度**判断，超限立即中止本次协议会话。
//
// 整行字节完整之后才做 UTF-8 解码——先解码再切行会把跨读取边界的
// 多字节字符切坏。
class ProtocolCodec {
public:
    // 最多记住多少个 session 的 seq 水位，防止长跑进程里映射无界增长。
    static constexpr int kMaxTrackedSessions = 16;

    explicit ProtocolCodec(int maxMessageBytes = kMaxMessageBytes);

    // 喂入一段到达的字节（一次 readyRead 不等于一条消息），返回本次新投递的信封。
    QList<Envelope> feed(const QByteArray& data);

    // 输入流结束（EOF/进程退出）。残留半包必须丢弃，绝不当整行解析。
    void finish();

    // 累计观测（字段名与 Framer 的公开属性对应，便于两端测试对拍）
    QHash<QString, int> errorCounts;
    QStringList unsupported;
    int duplicatesDropped = 0;
    bool aborted = false;

    [[nodiscard]] int errorTotal() const;
    [[nodiscard]] int bufferedBytes() const { return m_buffer.size(); }

private:
    void decodeLine(const QByteArray& line, QList<Envelope>& delivered);
    bool isReplay(const Envelope& envelope);

    int m_maxMessageBytes;
    QByteArray m_buffer;
    // (session_id, kind) → 已见最大 seq。request/response/event 分开计水位：
    // 两端可能用同一个 session_id，若不按 kind 分开会把合法消息误判为重放。
    QHash<QString, qint64> m_seenSeq;
    QStringList m_seenSeqOrder; // FIFO 驱逐顺序，对应 Python dict 的插入序
};

} // namespace Pet::Services
