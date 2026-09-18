#include "services/bridge/ProtocolMessage.hpp"

#include <QJsonArray>
#include <QJsonDocument>

#include <cmath>
#include <vector>

namespace Pet::Services {

namespace {

// ----------------------------------------------------------------------
// 正则（与 contracts.py 的 _RE_* 一致；\A...\z 语义）
// 每个校验函数持有**自己的**静态正则：绝对不能共享一个「按传入 pattern 构造」
// 的静态对象——那会在第一次调用时被固化成某一个 pattern，之后所有校验
// 都用错表（顺序依赖，测试二进制与宿主二进制可能初始化结果不同）。
// \z 只匹配主串末尾（连结尾换行都不放过），语义比 $ 严格。
// ----------------------------------------------------------------------
bool isSessionId(const QString& s)
{
    static const QRegularExpression re(QStringLiteral("\\A[A-Za-z0-9_.:-]{8,64}\\z"));
    return re.match(s).hasMatch();
}

bool isPetId(const QString& s)
{
    static const QRegularExpression re(QStringLiteral("\\A[A-Za-z0-9_-]{1,64}\\z"));
    return re.match(s).hasMatch();
}

bool isRequestId(const QString& s)
{
    static const QRegularExpression re(QStringLiteral("\\A[A-Za-z0-9_.:-]{1,64}\\z"));
    return re.match(s).hasMatch();
}

bool isMessageType(const QString& s)
{
    static const QRegularExpression re(QStringLiteral("\\A[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+\\z"));
    return s.size() <= 128 && re.match(s).hasMatch();
}

bool isAgentKey(const QString& s)
{
    static const QRegularExpression re(QStringLiteral("\\A[a-z][a-z0-9_]{0,31}\\z"));
    return re.match(s).hasMatch();
}

// ----------------------------------------------------------------------
// 字段类型判定（与 contracts.py 的 check_field 一一对应）
// Python 用 isinstance(v, int) and not isinstance(v, bool) 判定整数；
// JSON 里 bool 与 number 在 Qt 中本就互斥，只需补「必须为整数值」。
// ----------------------------------------------------------------------
bool isJsonValueInt(const QJsonValue& value)
{
    if (!value.isDouble()) {
        return false;
    }
    const double number = value.toDouble();
    return std::isfinite(number) && std::floor(number) == number;
}

bool isJsonValueStrList(const QJsonValue& value)
{
    if (!value.isArray()) {
        return false;
    }
    const QJsonArray array = value.toArray();
    for (const auto& item : array) {
        if (!item.isString()) {
            return false;
        }
    }
    return true;
}

bool checkBubble(const QJsonValue& value)
{
    if (value.isNull()) {
        return true; // Python _check_bubble(None) → True
    }
    if (!value.isObject()) {
        return false;
    }
    const QJsonObject object = value.toObject();
    constexpr int kMaxBubbleTextLen = 256;
    constexpr int kMinBubbleDurationMs = 500;
    constexpr int kMaxBubbleDurationMs = 20000;
    const auto keys = object.keys();
    for (const QString& key : keys) {
        if (key != QStringLiteral("text") && key != QStringLiteral("important")
            && key != QStringLiteral("duration_ms")) {
            return false;
        }
    }
    if (!object.contains(QStringLiteral("text"))) {
        return false;
    }
    const QJsonValue text = object.value(QStringLiteral("text"));
    if (!text.isString() || text.toString().size() < 1 || text.toString().size() > kMaxBubbleTextLen) {
        return false;
    }
    if (object.contains(QStringLiteral("important")) && !object.value(QStringLiteral("important")).isBool()) {
        return false;
    }
    if (object.contains(QStringLiteral("duration_ms"))) {
        const QJsonValue duration = object.value(QStringLiteral("duration_ms"));
        if (!isJsonValueInt(duration)) {
            return false;
        }
        const double ms = duration.toDouble();
        if (ms < kMinBubbleDurationMs || ms > kMaxBubbleDurationMs) {
            return false;
        }
    }
    return true;
}

bool checkActionId(const QJsonValue& value)
{
    constexpr int kMaxActionIdLen = 128;
    return value.isString() && !value.toString().isEmpty() && value.toString().size() <= kMaxActionIdLen;
}

bool checkActionList(const QJsonValue& value)
{
    constexpr int kMaxActions = 512;
    if (!value.isArray()) {
        return false;
    }
    const QJsonArray array = value.toArray();
    if (array.size() > kMaxActions) {
        return false;
    }
    for (const auto& item : array) {
        if (!checkActionId(item)) {
            return false;
        }
    }
    return true;
}

bool checkAgentsMap(const QJsonValue& value)
{
    if (!value.isObject()) {
        return false;
    }
    const QJsonObject object = value.toObject();
    for (auto it = object.begin(); it != object.end(); ++it) {
        if (!isAgentKey(it.key()) || !it.value().isObject()) {
            return false;
        }
        const QJsonObject item = it.value().toObject();
        for (const QString& key : item.keys()) {
            if (key != QStringLiteral("enabled") && key != QStringLiteral("kind")
                && key != QStringLiteral("display_name") && key != QStringLiteral("events_path")
                && key != QStringLiteral("busy_agents") && key != QStringLiteral("thinking_text")) {
                return false;
            }
        }
        const QJsonValue enabled = item.value(QStringLiteral("enabled"));
        if (!enabled.isBool()) {
            return false;
        }
        // kind 与 Worker 支持的三类适配器一致（contracts.py / schema 同步）
        static const QStringList supportedKinds = {
            QStringLiteral("custom"), QStringLiteral("antigravity"), QStringLiteral("chatgpt"),
        };
        if (!supportedKinds.contains(item.value(QStringLiteral("kind")).toString())) {
            return false;
        }
        for (const QString& textKey :
             {QStringLiteral("display_name"), QStringLiteral("events_path"), QStringLiteral("thinking_text")}) {
            if (item.contains(textKey) && !item.value(textKey).isString()) {
                return false;
            }
        }
        if (item.contains(QStringLiteral("busy_agents"))
            && !isJsonValueStrList(item.value(QStringLiteral("busy_agents")))) {
            return false;
        }
    }
    return true;
}

bool checkPolicy(const QJsonValue& value)
{
    if (!value.isObject()) {
        return false;
    }
    const QJsonObject object = value.toObject();
    const QStringList boolFields = {QStringLiteral("notify_state"), QStringLiteral("notify_done"),
                                    QStringLiteral("notify_activity")};
    const QList<QPair<QString, int>> msFields = {
        {QStringLiteral("min_interval_ms"), 60000},
        {QStringLiteral("activity_interval_ms"), 600000},
        {QStringLiteral("activity_global_min_ms"), 600000},
        {QStringLiteral("activity_same_label_ms"), 600000},
        {QStringLiteral("done_confirm_ms"), 10000},
        {QStringLiteral("done_cooldown_ms"), 600000},
    };
    for (const QString& key : object.keys()) {
        if (!boolFields.contains(key) && !std::any_of(msFields.cbegin(), msFields.cend(),
                                                      [&](const auto& pair) { return pair.first == key; })) {
            return false;
        }
    }
    for (const QString& key : boolFields) {
        if (object.contains(key) && !object.value(key).isBool()) {
            return false;
        }
    }
    for (const auto& [key, maximum] : msFields) {
        if (!object.contains(key)) {
            continue;
        }
        const QJsonValue item = object.value(key);
        if (!isJsonValueInt(item) || item.toDouble() < 0 || item.toDouble() > maximum) {
            return false;
        }
    }
    return true;
}

bool checkField(const QString& fieldType, const QJsonValue& value)
{
    if (fieldType == QStringLiteral("any")) {
        return true;
    }
    if (fieldType == QStringLiteral("str")) {
        return value.isString();
    }
    if (fieldType == QStringLiteral("str_or_null")) {
        return value.isNull() || value.isString();
    }
    if (fieldType == QStringLiteral("bool")) {
        return value.isBool();
    }
    if (fieldType == QStringLiteral("int")) {
        return isJsonValueInt(value);
    }
    if (fieldType == QStringLiteral("int_min0")) {
        return isJsonValueInt(value) && value.toDouble() >= 0;
    }
    if (fieldType == QStringLiteral("int_pos")) {
        return isJsonValueInt(value) && value.toDouble() >= 1;
    }
    if (fieldType == QStringLiteral("int_ttl")) {
        return isJsonValueInt(value) && value.toDouble() >= 1 && value.toDouble() <= 30000;
    }
    if (fieldType == QStringLiteral("int_shutdown_timeout")) {
        return isJsonValueInt(value) && value.toDouble() >= 0 && value.toDouble() <= 2000;
    }
    if (fieldType == QStringLiteral("str_list")) {
        return isJsonValueStrList(value);
    }
    if (fieldType == QStringLiteral("obj")) {
        return value.isObject();
    }
    if (fieldType == QStringLiteral("agent_key")) {
        return value.isString() && isAgentKey(value.toString());
    }
    if (fieldType == QStringLiteral("agent_state")) {
        static const QStringList states = {QStringLiteral("idle"), QStringLiteral("thinking"),
                                           QStringLiteral("working"), QStringLiteral("attention"),
                                           QStringLiteral("sleeping"), QStringLiteral("error")};
        return value.isString() && states.contains(value.toString());
    }
    if (fieldType == QStringLiteral("action_id")) {
        return checkActionId(value);
    }
    if (fieldType == QStringLiteral("action_list")) {
        return checkActionList(value);
    }
    if (fieldType == QStringLiteral("bubble")) {
        return checkBubble(value);
    }
    if (fieldType == QStringLiteral("agents_map")) {
        return checkAgentsMap(value);
    }
    if (fieldType == QStringLiteral("policy_cfg")) {
        return checkPolicy(value);
    }
    if (fieldType.startsWith(QStringLiteral("enum:"))) {
        const QStringList values = fieldType.mid(5).split(QLatin1Char('|'));
        return value.isString() && values.contains(value.toString());
    }
    return false;
}

// ----------------------------------------------------------------------
// payload 规格（与 contracts.PAYLOAD_SPECS 一一对应）
// kind 为 nullptr 表示三种 kind 都允许（ping/pong 双向共用）。
// ----------------------------------------------------------------------
struct FieldRule {
    const char* name;
    const char* ft;
    bool required;
};

struct MessageSpec {
    QString type;
    QString kind; // 空串：三种 kind 都允许（ping/pong 双向共用）
    std::vector<FieldRule> fields;
};

// 注意：type/kind 必须持有 QString 而不是 toLatin1().constData()——后者指向
// 临时 QByteArray 的内部缓冲，初始化表达式结束即悬空（UB），会让整张规格表
// 在运行期变成垃圾、所有出向消息被误判为「未定义类型」。
const MessageSpec* payloadSpecFor(const QString& type)
{
    static const std::vector<MessageSpec> specs = {
        {kMsgRuntimeHello, QStringLiteral("request"),
         {{"host_version", "str", true}, {"required_capabilities", "str_list", true}}},
        {kMsgRuntimeReady, QStringLiteral("response"),
         {{"worker_version", "str", true}, {"capabilities", "str_list", true}, {"pid", "int_pos", false}}},
        {kMsgConfigSnapshot, QStringLiteral("event"),
         {{"revision", "int_min0", true},
          {"agent_backend", "enum:native|python", true},
          {"agents", "agents_map", true},
          {"policy", "policy_cfg", false}}},
        {kMsgPetSnapshot, QStringLiteral("event"),
         {{"generation", "int_min0", true},
          {"character", "str", true},
          {"visible", "bool", true},
          {"interaction_locked", "bool", true},
          {"actions", "action_list", true}}},
        {kMsgPetEvent, QStringLiteral("event"),
         {{"name", "enum:click|drag_start|drag_end|action_finished|hidden|shown|character_changed", true},
          {"generation", "int_min0", true},
          {"action_id", "action_id", false}}},
        {kMsgAgentStateChanged, QStringLiteral("event"),
         {{"agent", "agent_key", true},
          {"state", "agent_state", true},
          {"generation", "int_min0", true},
          {"config_revision", "int_min0", true},
          {"tool", "str", false}}},
        {kMsgBehaviorPropose, QStringLiteral("request"),
         {{"action_id", "action_id", true},
          {"generation", "int_min0", true},
          {"config_revision", "int_min0", true},
          {"ttl_ms", "int_ttl", false},
          {"agent", "agent_key", false},
          {"bubble", "bubble", false}}},
        {kMsgBehaviorResult, QStringLiteral("response"),
         {{"status", "enum:accepted|rejected|expired", true}, {"reason", "str", false}}},
        {kMsgConfigPatchRequest, QStringLiteral("request"),
         {{"expected_revision", "int_min0", true}, {"patch", "obj", true}}},
        {kMsgConfigPatchResult, QStringLiteral("response"),
         {{"status", "enum:applied|rejected", true}, {"reason", "str", false}, {"revision", "int_min0", false}}},
        {kMsgRuntimePing, QString(), {}},
        {kMsgRuntimePong, QString(), {}},
        {kMsgRuntimeShutdown, QStringLiteral("event"), {{"timeout_ms", "int_shutdown_timeout", false}}},
        {kMsgUnsupportedMessage, QStringLiteral("response"),
         {{"reason", "enum:unknown_type|invalid_envelope|unsupported_version|payload_too_large|unsupported_field",
           true},
          {"detail", "str", false}}},
    };
    for (const MessageSpec& spec : specs) {
        if (type == spec.type) {
            return &spec;
        }
    }
    return nullptr;
}

DecodeOutcome fail(EnvelopeError error, QString detail, QString messageType = {})
{
    return DecodeOutcome{error, std::move(detail), std::move(messageType)};
}

// payload 校验：与 contracts._validate_payload 一致——缺字段、多字段、
// 字段非法都整条拒绝，绝不做部分应用。
DecodeOutcome validatePayload(const QString& type, const QJsonValue& payloadValue, Envelope* out)
{
    if (!payloadValue.isObject()) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("payload 必须是对象"));
    }
    const QJsonObject payload = payloadValue.toObject();
    const MessageSpec* spec = payloadSpecFor(type);

    QStringList missing;
    QStringList allowed;
    if (spec == nullptr) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("未定义的消息类型"));
    }
    for (const FieldRule& rule : spec->fields) {
        allowed << QString::fromLatin1(rule.name);
        if (rule.required && !payload.contains(QLatin1String(rule.name))) {
            missing << QString::fromLatin1(rule.name);
        }
    }
    if (!missing.isEmpty()) {
        missing.sort();
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("payload 缺少字段 %1").arg(missing.join(u',')));
    }
    for (const QString& key : payload.keys()) {
        if (!allowed.contains(key)) {
            return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("payload 含未定义字段 %1").arg(key));
        }
    }
    for (const FieldRule& rule : spec->fields) {
        const QJsonValue value = payload.value(QLatin1String(rule.name));
        if (rule.required && !checkField(QString::fromLatin1(rule.ft), value)) {
            return fail(EnvelopeError::InvalidEnvelope,
                        QStringLiteral("payload.%1 类型/取值非法").arg(QString::fromLatin1(rule.name)));
        }
        if (!rule.required && payload.contains(QLatin1String(rule.name))
            && !checkField(QString::fromLatin1(rule.ft), value)) {
            return fail(EnvelopeError::InvalidEnvelope,
                        QStringLiteral("payload.%1 类型/取值非法").arg(QString::fromLatin1(rule.name)));
        }
    }
    if (out != nullptr) {
        out->payload = payload;
    }
    return DecodeOutcome{};
}

QString kindOfSpec(const QString& type)
{
    const MessageSpec* spec = payloadSpecFor(type);
    return spec == nullptr ? QString() : spec->kind;
}

} // namespace

QString kindToString(Kind kind)
{
    switch (kind) {
    case Kind::Request:
        return QStringLiteral("request");
    case Kind::Response:
        return QStringLiteral("response");
    case Kind::Event:
        return QStringLiteral("event");
    }
    return QStringLiteral("event");
}

bool kindFromString(const QString& text, Kind* out)
{
    if (text == QStringLiteral("request")) {
        *out = Kind::Request;
        return true;
    }
    if (text == QStringLiteral("response")) {
        *out = Kind::Response;
        return true;
    }
    if (text == QStringLiteral("event")) {
        *out = Kind::Event;
        return true;
    }
    return false;
}

DecodeOutcome validateEnvelope(const QJsonObject& message, Envelope* out)
{
    // Python 先查多余字段再查缺失字段；两者都归 invalid_envelope。
    static const char* kEnvelopeKeys[] = {"protocol_version", "session_id", "seq",  "kind",
                                          "type",             "pet_id",     "request_id", "payload"};
    constexpr int kEnvelopeKeyCount = 8;
    if (message.size() > kEnvelopeKeyCount) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("含未定义字段"));
    }
    for (const char* key : kEnvelopeKeys) {
        if (!message.contains(QLatin1String(key))) {
            return fail(EnvelopeError::InvalidEnvelope,
                        QStringLiteral("缺少字段 %1").arg(QString::fromLatin1(key)));
        }
    }

    const QJsonValue version = message.value(QStringLiteral("protocol_version"));
    if (!isJsonValueInt(version)) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("protocol_version 必须是整数"));
    }
    if (version.toInt() != kProtocolVersion) {
        // 主版本不兼容：不投递、不中止会话，宿主据此降级为 native。
        return fail(EnvelopeError::UnsupportedVersion,
                    QStringLiteral("protocol_version=%1").arg(version.toInt()));
    }

    Kind kind = Kind::Event;
    if (!kindFromString(message.value(QStringLiteral("kind")).toString(), &kind)) {
        return fail(EnvelopeError::InvalidEnvelope,
                    QStringLiteral("kind=%1").arg(message.value(QStringLiteral("kind")).toVariant().toString()));
    }

    const QString type = message.value(QStringLiteral("type")).toString();
    if (!isMessageType(type)) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("type 非法"));
    }

    const QString sessionId = message.value(QStringLiteral("session_id")).toString();
    if (!isSessionId(sessionId)) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("session_id 非法"));
    }

    const QString petId = message.value(QStringLiteral("pet_id")).toString();
    if (!isPetId(petId)) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("pet_id 非法"));
    }

    const QJsonValue seq = message.value(QStringLiteral("seq"));
    if (!isJsonValueInt(seq) || seq.toDouble() < 0) {
        return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("seq 必须是非负整数"));
    }

    const QJsonValue requestIdValue = message.value(QStringLiteral("request_id"));
    QString requestId;
    if (kind == Kind::Event) {
        if (!requestIdValue.isNull()) {
            return fail(EnvelopeError::InvalidEnvelope, QStringLiteral("event 的 request_id 必须是 null"));
        }
    } else if (!requestIdValue.isString()
               || !isRequestId(requestIdValue.toString())) {
        return fail(EnvelopeError::InvalidEnvelope,
                    QStringLiteral("%1 的 request_id 必须是非空字符串").arg(kindToString(kind)));
    } else {
        requestId = requestIdValue.toString();
    }

    if (payloadSpecFor(type) == nullptr) {
        // 信封合法但类型不认识：只记录，绝不触发默认动作。
        return fail(EnvelopeError::UnknownType, QString(), type);
    }

    if (const QString expected = kindOfSpec(type); !expected.isEmpty() && kindToString(kind) != expected) {
        return fail(EnvelopeError::InvalidEnvelope,
                    QStringLiteral("%1 要求 kind=%2").arg(type, expected));
    }

    if (out != nullptr) {
        *out = Envelope{};
        out->protocolVersion = version.toInt();
        out->sessionId = sessionId;
        out->seq = static_cast<qint64>(seq.toDouble());
        out->kind = kind;
        out->type = type;
        out->petId = petId;
        out->requestId = requestId;
    }
    return validatePayload(type, message.value(QStringLiteral("payload")), out);
}

Envelope makeEnvelope(Kind kind, const QString& type, const QJsonObject& payload, const QString& requestId,
                      const QString& sessionId, qint64 seq, const QString& petId)
{
    if (kind == Kind::Event) {
        Q_ASSERT(requestId.isEmpty());
    } else {
        Q_ASSERT(!requestId.isEmpty());
    }
    Envelope envelope;
    envelope.protocolVersion = kProtocolVersion;
    envelope.sessionId = sessionId;
    envelope.seq = seq;
    envelope.kind = kind;
    envelope.type = type;
    envelope.petId = petId;
    envelope.requestId = requestId;
    envelope.payload = payload;
    return envelope;
}

QByteArray encodeLine(const Envelope& envelope, bool* ok, QString* rejection, int maxMessageBytes)
{
    if (ok != nullptr) {
        *ok = false;
    }
    if (rejection != nullptr) {
        rejection->clear();
    }
    // event 强制 request_id=null；request/response 必须带非空 request_id
    QJsonObject object;
    object.insert(QStringLiteral("protocol_version"), envelope.protocolVersion);
    object.insert(QStringLiteral("session_id"), envelope.sessionId);
    object.insert(QStringLiteral("seq"), static_cast<double>(envelope.seq));
    object.insert(QStringLiteral("kind"), kindToString(envelope.kind));
    object.insert(QStringLiteral("type"), envelope.type);
    object.insert(QStringLiteral("pet_id"), envelope.petId);
    object.insert(QStringLiteral("request_id"),
                  envelope.kind == Kind::Event ? QJsonValue(QJsonValue::Null) : QJsonValue(envelope.requestId));
    object.insert(QStringLiteral("payload"), envelope.payload);

    // 写前先过一遍契约校验：宁可在这里丢一条自己写错的消息，
    // 也不要让 Worker 收到非法行、进而判定整条通道损坏并重启。
    const DecodeOutcome outcome = validateEnvelope(object);
    if (outcome.error != EnvelopeError::None) {
        if (rejection != nullptr) {
            *rejection = outcome.detail.isEmpty() ? QStringLiteral("validation failed") : outcome.detail;
        }
        return {};
    }

    QByteArray line = QJsonDocument(object).toJson(QJsonDocument::Compact);
    line.append('\n');
    // 字符串内的换行由 JSON 编码自动转义，一条消息永远只占一行；
    // 但总长度超限仍要拒绝，避免写坏对端的分帧器。
    if (line.size() > maxMessageBytes) {
        if (rejection != nullptr) {
            *rejection = QStringLiteral("payload_too_large");
        }
        return {};
    }
    if (ok != nullptr) {
        *ok = true;
    }
    return line;
}

} // namespace Pet::Services
