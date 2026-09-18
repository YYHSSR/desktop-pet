# -*- coding: utf-8 -*-
"""分帧契约测试：直接消费 ``protocol/fixtures/framing_cases.json``。

这是两端同步的**唯一判据**——C++ 的 ``ProtocolCodec`` 必须对同一份用例得到相同
结果。因此这里不做任何「本地加料」的断言：用例说什么，就断言什么。
"""

from __future__ import annotations

import pytest
from _support import load_framing_cases

from pet_worker.contracts import MAX_MESSAGE_BYTES
from pet_worker.transport import Framer

CASES = load_framing_cases()["cases"]
CASE_IDS = [case["name"] for case in CASES]


def _build_stream(parts: list[dict]) -> bytes:
    """按用例模型拼出完整字节流。"""
    chunks: list[bytes] = []
    for part in parts:
        if "bytes" in part:
            data = bytes(part["bytes"])
        else:
            data = str(part["text"]).encode("utf-8")
        chunks.append(data * int(part.get("repeat", 1)))
    return b"".join(chunks)


def _split_offsets(stream: bytes, spec: list[dict] | None) -> list[int]:
    """把 split_points 解析成字节偏移列表（已排序去重、剔除边界值）。"""
    offsets: list[int] = []
    for point in spec or []:
        if "byte_offset" in point:
            index = int(point["byte_offset"])
        else:
            needle = str(point["after_substring"]).encode("utf-8")
            index = stream.index(needle) + len(needle) + int(point.get("offset", 0))
        if 0 < index < len(stream):
            offsets.append(index)
    return sorted(set(offsets))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_framing_case(case: dict) -> None:
    stream = _build_stream(case["stream"])
    offsets = _split_offsets(stream, case.get("split_points"))

    if case.get("require_mid_codepoint_split"):
        # 切点必须真的落在多字节字符内部，否则这条用例退化成无意义的 ASCII 用例，
        # 会给出「UTF-8 跨边界处理正确」的假信号。
        assert any(0x80 <= stream[index] <= 0xBF for index in offsets), (
            f"{case['name']}: 切点没有落在 UTF-8 多字节字符内部"
        )

    framer = Framer(max_message_bytes=load_framing_cases()["max_message_bytes"])
    delivered: list[dict] = []
    previous = 0
    for offset in offsets:
        delivered.extend(framer.feed(stream[previous:offset]))
        previous = offset
    delivered.extend(framer.feed(stream[previous:]))
    framer.finish()

    expect = case["expect"]
    assert [message["type"] for message in delivered] == expect["messages"], case["name"]
    assert framer.error_total == sum(item["count"] for item in expect["errors"]), case["name"]
    assert {item["kind"]: item["count"] for item in framer.error_list()} == {
        item["kind"]: item["count"] for item in expect["errors"]
    }, case["name"]
    assert framer.unsupported == expect["unsupported"], case["name"]
    assert framer.duplicates_dropped == expect["duplicates_dropped"], case["name"]
    assert framer.aborted == expect["abort"], case["name"]


def test_aborted_framer_stops_delivering() -> None:
    """会话中止后再投喂任何字节都必须无效——否则超限防线等于没有。"""
    case = next(item for item in CASES if item["name"] == "oversize_unterminated_aborts_session")
    framer = Framer(max_message_bytes=MAX_MESSAGE_BYTES)
    framer.feed(b"x" * (MAX_MESSAGE_BYTES + 1))
    assert framer.aborted is True

    good = _build_stream(
        [
            {
                "text": '{"protocol_version":1,"session_id":"s","seq":0,"kind":"request",'
                '"type":"runtime.ping","pet_id":"main","request_id":"p1","payload":{}}\n'
            }
        ]
    )
    assert framer.feed(good) == []
    assert framer.messages == []
    assert case["expect"]["abort"] is True
