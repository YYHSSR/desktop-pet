#pragma once

#include <QByteArray>
#include <QJsonObject>
#include <QString>
#include <QVariant>

namespace Pet::Services {

// 桥接协议契约的单一来源（C++ 侧）。
//
// 与 c++-python/protocol/envelope.schema.json、worker/src/pet_worker/contracts.py
// 一一对应。任何字段、枚举、上限的修改都必须先改 schema 与夹具，再同步三处实现
// ——不允许两端各自「就地修好」。
//
// 本文件不做任何 I/O：只做「JSON 对象 → 信封」的校验与编解码。

// ----------------------------------------------------------------------
// 版本与上限（schema 的 x-limits）
// ----------------------------------------------------------------------
inline constexpr int kProtocolVersion = 1;
inline constexpr int kMaxMessageBytes = 65536;
inline constexpr int kMaxOutboundQueueMessages = 256;
inline constexpr qint64 kMaxOutboundQueueBytes = 1048576;
inline constexpr int kHandshakeTimeoutMs = 5000;
inline constexpr int kHeartbeatIntervalMs = 2000;
inline constexpr int kHeartbeatMissThreshold = 3;
inline constexpr int kShutdownGraceMs = 2000;
inline constexpr int kDefaultProposalTtlMs = 2000;

inline constexpr int kRestartBackoffMs[] = {1000, 2000, 4000};
inline constexpr int kRestartWindowMs = 60000;
inline constexpr int kMaxRestartsPerWindow = 3;

inline constexpr auto kDefaultPetId = "main";

// ----------------------------------------------------------------------
// 分帧错误码（与 fixtures/framing_cases.json 的 error_kinds 一致）
// ----------------------------------------------------------------------
inline const QString kErrInvalidJson = QStringLiteral("invalid_json");
inline const QString kErrInvalidUtf8 = QStringLiteral("invalid_utf8");
inline const QString kErrInvalidEnvelope = QStringLiteral("invalid_envelope");
inline const QString kErrPayloadTooLarge = QStringLiteral("payload_too_large");
inline const QString kErrUnsupportedVersion = QStringLiteral("unsupported_version");
inline const QString kErrTruncatedOnEof = QStringLiteral("truncated_on_eof");
// 不算「错误计数」但需要单独上报的两类
inline const QString kErrUnknownType = QStringLiteral("unknown_type");
inline const QString kErrDuplicateSeq = QStringLiteral("duplicate_seq");

// ----------------------------------------------------------------------
// 消息类型
// ----------------------------------------------------------------------
inline const QString kMsgRuntimeHello = QStringLiteral("runtime.hello");
inline const QString kMsgRuntimeReady = QStringLiteral("runtime.ready");
inline const QString kMsgConfigSnapshot = QStringLiteral("config.snapshot");
inline const QString kMsgPetSnapshot = QStringLiteral("pet.snapshot");
inline const QString kMsgPetEvent = QStringLiteral("pet.event");
inline const QString kMsgAgentStateChanged = QStringLiteral("agent.state_changed");
inline const QString kMsgBehaviorPropose = QStringLiteral("behavior.propose");
inline const QString kMsgBehaviorResult = QStringLiteral("behavior.result");
inline const QString kMsgConfigPatchRequest = QStringLiteral("config.patch_request");
inline const QString kMsgConfigPatchResult = QStringLiteral("config.patch_result");
inline const QString kMsgRuntimePing = QStringLiteral("runtime.ping");
inline const QString kMsgRuntimePong = QStringLiteral("runtime.pong");
inline const QString kMsgRuntimeShutdown = QStringLiteral("runtime.shutdown");
inline const QString kMsgUnsupportedMessage = QStringLiteral("runtime.unsupported_message");

// Worker 回报的能力（宿主按 required_capabilities 判定是否降级）
inline const QStringList kWorkerCapabilities = {
    QStringLiteral("state"),
    QStringLiteral("behavior"),
    QStringLiteral("custom_agent"),
    QStringLiteral("config_patch"),
};

// 系统保留动作：不是角色包里的真实动作，由宿主仲裁器直接解释
inline const QString kSystemActionIdle = QStringLiteral("system/idle");
inline const QString kSystemActionBubble = QStringLiteral("system/bubble");

// ----------------------------------------------------------------------
// 信封
// ----------------------------------------------------------------------
enum class Kind {
    Request,
    Response,
    Event,
};

[[nodiscard]] QString kindToString(Kind kind);
[[nodiscard]] bool kindFromString(const QString& text, Kind* out);

struct Envelope {
    int protocolVersion = kProtocolVersion;
    QString sessionId;
    qint64 seq = 0;
    Kind kind = Kind::Event;
    QString type;
    QString petId = QString::fromLatin1(kDefaultPetId);
    QString requestId; // event 恒为空；request/response 必填
    QJsonObject payload;
};

enum class EnvelopeError {
    None,
    InvalidEnvelope,    // 结构/字段/枚举非法
    UnsupportedVersion, // 主版本不匹配：不投递、不中止会话
    UnknownType,        // 信封合法但 type 未定义：绝不触发默认动作
};

struct DecodeOutcome {
    EnvelopeError error = EnvelopeError::None;
    QString detail;      // 仅供诊断，绝不携带 payload 内容或用户路径
    QString messageType; // UnknownType 时填充
};

// 校验一个已解析的 JSON 对象是否为合法信封。合法且 out 非空时填充 out。
// 校验顺序与 contracts.validate_envelope 完全一致——顺序错了，
// 超限/未知版本/未知类型的归类会跟着错。
[[nodiscard]] DecodeOutcome validateEnvelope(const QJsonObject& object, Envelope* out = nullptr);

// 构造出向信封。event 强制 requestId 为空；request/response 必须显式给出
// requestId——关联关系一旦靠猜就会丢回执。
[[nodiscard]] Envelope makeEnvelope(Kind kind, const QString& type, const QJsonObject& payload,
                                    const QString& requestId, const QString& sessionId, qint64 seq,
                                    const QString& petId = QString::fromLatin1(kDefaultPetId));

// 把信封序列化成一行 UTF-8 字节（含结尾 \n）。写前先过一遍契约校验：
// 宁可在这里丢一条自己写错的消息，也不要让 Worker 收到非法行、
// 进而判定整条通道损坏并重启。失败（非法或超限）时返回空字节串，
// rejection 非空时携带被拒原因（供诊断，不含 payload 内容）。
[[nodiscard]] QByteArray encodeLine(const Envelope& envelope, bool* ok = nullptr,
                                    QString* rejection = nullptr, int maxMessageBytes = kMaxMessageBytes);

} // namespace Pet::Services
