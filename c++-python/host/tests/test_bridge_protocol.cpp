// 桥接协议契约测试：
// 1) 直接消费 c++-python/protocol/fixtures/framing_cases.json——这是两端同步的
//    **唯一判据**，C++ 的 ProtocolCodec 必须对同一份用例得到与 Python 相同的结果，
//    因此这里不做任何「本地加料」的分帧断言；
// 2) 信封编解码的出向补充用例（event/request_id 耦合、超限拒绝、往返一致）。
#include "services/bridge/ProtocolCodec.hpp"
#include "services/bridge/ProtocolMessage.hpp"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <algorithm>
#include <cassert>
#include <iostream>

#ifndef PET_PROTOCOL_DIR
#define PET_PROTOCOL_DIR "."
#endif

namespace {

QJsonObject loadFramingCases()
{
    QFile file(QStringLiteral(PET_PROTOCOL_DIR) + QStringLiteral("/fixtures/framing_cases.json"));
    assert(file.open(QIODevice::ReadOnly));
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    assert(document.isObject());
    return document.object();
}

QByteArray buildStream(const QJsonArray& parts)
{
    QByteArray stream;
    for (const auto& partValue : parts) {
        const QJsonObject part = partValue.toObject();
        QByteArray data;
        if (part.contains(QStringLiteral("bytes"))) {
            const QJsonArray bytes = part.value(QStringLiteral("bytes")).toArray();
            for (const auto& byte : bytes) {
                data.append(static_cast<char>(byte.toInt()));
            }
        } else {
            data = part.value(QStringLiteral("text")).toString().toUtf8();
        }
        stream += data.repeated(part.contains(QStringLiteral("repeat")) ? part.value(QStringLiteral("repeat")).toInt()
                                                                        : 1);
    }
    return stream;
}

// 把 split_points 解析成字节偏移列表（已排序去重、剔除边界值）
QList<qsizetype> splitOffsets(const QByteArray& stream, const QJsonArray& spec)
{
    QList<qsizetype> offsets;
    for (const auto& pointValue : spec) {
        const QJsonObject point = pointValue.toObject();
        qsizetype index = -1;
        if (point.contains(QStringLiteral("byte_offset"))) {
            index = point.value(QStringLiteral("byte_offset")).toInt();
        } else {
            const QByteArray needle =
                point.value(QStringLiteral("after_substring")).toString().toUtf8();
            index = stream.indexOf(needle) + needle.size() + point.value(QStringLiteral("offset")).toInt();
        }
        if (index > 0 && index < stream.size()) {
            offsets.append(index);
        }
    }
    std::sort(offsets.begin(), offsets.end());
    offsets.erase(std::unique(offsets.begin(), offsets.end()), offsets.end());
    return offsets;
}

void testFramingCases()
{
    const QJsonObject cases = loadFramingCases();
    const int maxMessageBytes = cases.value(QStringLiteral("max_message_bytes")).toInt();

    for (const auto& caseValue : cases.value(QStringLiteral("cases")).toArray()) {
        const QJsonObject testCase = caseValue.toObject();
        const QString name = testCase.value(QStringLiteral("name")).toString();
        const QJsonObject expect = testCase.value(QStringLiteral("expect")).toObject();

        const QByteArray stream = buildStream(testCase.value(QStringLiteral("stream")).toArray());
        const QList<qsizetype> offsets =
            splitOffsets(stream, testCase.value(QStringLiteral("split_points")).toArray());

        if (testCase.value(QStringLiteral("require_mid_codepoint_split")).toBool()) {
            // 切点必须真的落在多字节字符内部，否则这条用例退化成无意义的 ASCII 用例
            bool midCodepoint = false;
            for (const qsizetype offset : offsets) {
                const auto byte = static_cast<unsigned char>(stream.at(offset));
                midCodepoint = midCodepoint || (byte >= 0x80 && byte <= 0xBF);
            }
            assert(midCodepoint && "切点没有落在 UTF-8 多字节字符内部");
        }

        Pet::Services::ProtocolCodec codec(maxMessageBytes);
        QList<Pet::Services::Envelope> delivered;
        qsizetype previous = 0;
        for (const qsizetype offset : offsets) {
            delivered += codec.feed(stream.mid(previous, offset - previous));
            previous = offset;
        }
        delivered += codec.feed(stream.mid(previous));
        codec.finish();

        QStringList deliveredTypes;
        for (const auto& envelope : delivered) {
            deliveredTypes << envelope.type;
        }
        QStringList expectedTypes;
        for (const auto& type : expect.value(QStringLiteral("messages")).toArray()) {
            expectedTypes << type.toString();
        }
        assert(deliveredTypes == expectedTypes && qPrintable(QStringLiteral("messages 不一致: %1").arg(name)));

        QHash<QString, int> expectedErrors;
        int expectedErrorTotal = 0;
        for (const auto& error : expect.value(QStringLiteral("errors")).toArray()) {
            const QJsonObject item = error.toObject();
            expectedErrors.insert(item.value(QStringLiteral("kind")).toString(),
                                  item.value(QStringLiteral("count")).toInt());
            expectedErrorTotal += item.value(QStringLiteral("count")).toInt();
        }
        assert(codec.errorTotal() == expectedErrorTotal && qPrintable(QStringLiteral("错误总数不一致: %1").arg(name)));
        assert(codec.errorCounts == expectedErrors && qPrintable(QStringLiteral("错误分类不一致: %1").arg(name)));
        assert(codec.unsupported == expect.value(QStringLiteral("unsupported")).toVariant().toStringList()
               && qPrintable(QStringLiteral("unsupported 不一致: %1").arg(name)));
        assert(codec.duplicatesDropped == expect.value(QStringLiteral("duplicates_dropped")).toInt()
               && qPrintable(QStringLiteral("重放计数不一致: %1").arg(name)));
        assert(codec.aborted == expect.value(QStringLiteral("abort")).toBool()
               && qPrintable(QStringLiteral("abort 不一致: %1").arg(name)));
    }
    std::cout << "framing_cases: all ok" << std::endl;
}

void testAbortedCodecStopsDelivering()
{
    // 会话中止后再投喂任何字节都必须无效——否则超限防线等于没有。
    Pet::Services::ProtocolCodec codec(Pet::Services::kMaxMessageBytes);
    codec.feed(QByteArray(static_cast<qsizetype>(Pet::Services::kMaxMessageBytes) + 1, 'x'));
    assert(codec.aborted);

    const QByteArray good = R"({"protocol_version":1,"session_id":"s","seq":0,"kind":"request",)"
                            R"("type":"runtime.ping","pet_id":"main","request_id":"p1","payload":{}})"
                            "\n";
    assert(codec.feed(good).isEmpty());
    std::cout << "aborted codec: ok" << std::endl;
}

void testEncodeDecodeRoundtrip()
{
    using namespace Pet::Services;
    // 出向信封：中文文案按 UTF-8 原样传输，一次编解码往返无损
    QJsonObject bubble;
    bubble.insert(QStringLiteral("text"), QStringLiteral("干完活啦～"));
    bubble.insert(QStringLiteral("important"), true);
    bubble.insert(QStringLiteral("duration_ms"), 4500);
    QJsonObject payload;
    payload.insert(QStringLiteral("action_id"), QStringLiteral("random/写代码"));
    payload.insert(QStringLiteral("generation"), 7);
    payload.insert(QStringLiteral("config_revision"), 12);
    payload.insert(QStringLiteral("bubble"), bubble);

    const Envelope proposal = makeEnvelope(Kind::Request, kMsgBehaviorPropose, payload,
                                           QStringLiteral("prop-1"), QStringLiteral("sess-0001"), 3);
    bool ok = false;
    const QByteArray line = encodeLine(proposal, &ok);
    assert(ok);
    assert(line.endsWith('\n'));
    assert(line.count('\n') == 1); // 一条消息永远只占一行

    ProtocolCodec codec;
    const QList<Envelope> delivered = codec.feed(line);
    assert(delivered.size() == 1);
    assert(delivered.first().type == kMsgBehaviorPropose);
    assert(delivered.first().kind == Kind::Request);
    assert(delivered.first().requestId == QStringLiteral("prop-1"));
    assert(delivered.first().seq == 3);
    assert(delivered.first().payload.value(QStringLiteral("bubble")).toObject()
               .value(QStringLiteral("text")).toString()
           == QStringLiteral("干完活啦～"));
    std::cout << "encode/decode roundtrip: ok" << std::endl;
}

void testOutboundValidation()
{
    using namespace Pet::Services;
    bool ok = true;

    // event 的 request_id 必须·必须为空：编码前的契约校验必须拦下
    Envelope badEvent = makeEnvelope(Kind::Event, kMsgPetEvent, {}, QString(), QStringLiteral("sess-0001"), 0);
    badEvent.requestId = QStringLiteral("not-null");
    assert(encodeLine(badEvent, &ok).isEmpty());
    assert(!ok);

    // 缺 request_id 的 response 同样必须被拦下
    Envelope badResponse = makeEnvelope(Kind::Response, kMsgBehaviorResult, {}, QString(),
                                        QStringLiteral("sess-0001"), 0);
    assert(encodeLine(badResponse, &ok).isEmpty());
    assert(!ok);

    // 合法的 event：request_id 自动落成 null，且能被对端分帧器收下
    const Envelope event =
        makeEnvelope(Kind::Event, kMsgPetEvent, {{"name", "drag_start"}, {"generation", 1}},
                     QString(), QStringLiteral("sess-0001"), 0);
    const QByteArray line = encodeLine(event, &ok);
    assert(ok && line.contains("\"request_id\":null"));

    // 出向超限同样拒绝：写坏对端的分帧器比丢一条消息严重得多
    Envelope huge = makeEnvelope(Kind::Request, kMsgBehaviorPropose, {}, QStringLiteral("r"),
                                 QStringLiteral("sess-0001"), 0);
    QJsonObject big;
    big.insert(QStringLiteral("action_id"), QString(Pet::Services::kMaxMessageBytes, QLatin1Char('x')));
    huge.payload = big;
    assert(encodeLine(huge, &ok).isEmpty());
    assert(!ok);
    std::cout << "outbound validation: ok" << std::endl;
}

} // namespace

int main()
{
    testFramingCases();
    testAbortedCodecStopsDelivering();
    testEncodeDecodeRoundtrip();
    testOutboundValidation();
    return 0;
}
