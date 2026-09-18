# 协议夹具（两端共用）

这些文件是 C++ 宿主与 Python Worker 的**共同契约**。任何一端修改协议，都必须先改这里，再让两端同时通过。

## 目录

| 路径 | 用途 |
|---|---|
| `valid/*.json` | 单条合法信封。既用于 JSON 编解码往返测试，也用于字段校验测试。 |
| `framing_cases.json` | 字节流分帧与错误隔离用例。同一份用例必须被 C++ `ProtocolCodec` 与 Python `pet_worker.transport` 同时消费。 |

## valid/ 的字段约定

- 必填：`protocol_version`、`session_id`、`seq`、`kind`、`type`、`pet_id`、`request_id`、`payload`。
- `kind` 为 `request` / `response` 时，`request_id` 必须是非空字符串。
- `kind` 为 `event` 时，`request_id` 必须是 `null`。
- `behavior.propose` 需要 `behavior.result` 回执，因此是 **request**，不是 event。
- `runtime.shutdown` 是 **event**：进程退出即回执，没有可返回的结果。
- `additionalProperties` 一律为 false：出现未定义字段即整条拒绝，不做部分应用。

## framing_cases.json 的模型

每条用例包含：

- `stream`：完整字节流，由若干 part 顺次拼接。
  - `{ "text": "…", "repeat": N }`：按 UTF-8 编码，可重复 N 次。
  - `{ "bytes": [0-255, …] }`：逐字节追加，用于构造非法 UTF-8。
  - JSON 字符串中的 `\r` `\n` 会还原为真实字节。
- `split_points`（可选）：把字节流切成多次「到达」。每一项为
  - `{ "byte_offset": N }`，或
  - `{ "after_substring": "<按字节搜索的子串>", "offset": N }`。
  省略表示一次到达。
- `require_mid_codepoint_split`（可选）：断言至少一个切点落在 UTF-8 多字节字符内部。
  切点算错时用例必须失败，而不是静默退化成一个无意义的 ASCII 用例。
- `expect`：`messages`（投递成功的 `type` 顺序）、`errors`（`kind` + `count`）、
  `unsupported`、`duplicates_dropped`、`abort`。

## 关键判定顺序

实现必须按这个顺序处理，否则 `oversize_line_terminated` 与
`oversize_unterminated_aborts_session` 会得到相反结果：

1. 先在缓冲区中扫描 `\n`。
2. 存在 `\n`：取出该整行，单独校验这一行的长度；超过 `max_message_bytes` 记
   `payload_too_large` 并丢弃该行，**不中止会话**。
3. 不存在 `\n`：此时才用缓冲区总长度判断；超过上限立即**中止本次协议会话**。

## 运行

```powershell
# C++
ctest --test-dir c++/build --output-on-failure -R bridge_protocol

# Python
python -m pytest c++-python/worker/tests -k framing
```

两端都用同一份 `framing_cases.json`。若一端通过而另一端失败，说明实现偏离了契约，
不允许通过修改夹具来「修好」。
