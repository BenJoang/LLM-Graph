from __future__ import annotations

import base64
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    BaseMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_from_dict,
)


MESSAGE_PAYLOAD_SCHEMA_VERSION = 1
_BINARY_TEXT_MARKER = "__llm_graph_utf8_base64_v1__"


class MessageCodecError(ValueError):
    """消息不能安全地写入或从持久化格式恢复。"""


class UnsupportedMessageTypeError(MessageCodecError):
    """消息类型不属于正式对话日志支持的类型。"""


def encode_json_value(value: Any) -> Any:
    """保留 JSONB 不接受的 NUL 文本，同时维持可逆往返。"""

    if isinstance(value, str) and "\x00" in value:
        return {
            _BINARY_TEXT_MARKER: base64.b64encode(
                value.encode("utf-8")
            ).decode("ascii")
        }
    if isinstance(value, list):
        return [encode_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [encode_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: encode_json_value(item) for key, item in value.items()}
    return value


def decode_json_value(value: Any) -> Any:
    if (
        isinstance(value, dict)
        and set(value) == {_BINARY_TEXT_MARKER}
        and isinstance(value[_BINARY_TEXT_MARKER], str)
    ):
        return base64.b64decode(
            value[_BINARY_TEXT_MARKER]
        ).decode("utf-8")
    if isinstance(value, list):
        return [decode_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: decode_json_value(item) for key, item in value.items()}
    return value


def event_type_for_message(message: BaseMessage) -> str:
    """将 LangChain 消息映射为 conversation_events.event_type。"""

    if isinstance(message, BaseMessageChunk):
        raise UnsupportedMessageTypeError(
            "流式 MessageChunk 不是完整消息，不能直接写入对话日志"
        )
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, SystemMessage):
        return "system"

    raise UnsupportedMessageTypeError(
        f"不支持持久化消息类型：{type(message).__name__}"
    )


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    """把完整 LangChain 消息转换为可写入 JSON/JSONB 的字典。"""

    if not isinstance(message, BaseMessage):
        raise TypeError("message 必须是 BaseMessage")

    event_type_for_message(message)

    try:
        data = message.model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise MessageCodecError(
            f"消息包含不能序列化为 JSON 的数据：{exc}"
        ) from exc

    return {
        "schema_version": MESSAGE_PAYLOAD_SCHEMA_VERSION,
        "type": message.type,
        "data": encode_json_value(data),
    }


def deserialize_message(payload: dict[str, Any]) -> BaseMessage:
    """从持久化字典恢复一条完整 LangChain 消息。"""

    if not isinstance(payload, dict):
        raise TypeError("payload 必须是 dict")

    schema_version = payload.get("schema_version")
    if schema_version != MESSAGE_PAYLOAD_SCHEMA_VERSION:
        raise MessageCodecError(
            "不支持的消息 payload 版本："
            f"{schema_version!r}"
        )

    message_type = payload.get("type")
    data = decode_json_value(payload.get("data"))
    if not isinstance(message_type, str) or not message_type:
        raise MessageCodecError("消息 payload 缺少有效的 type")
    if not isinstance(data, dict):
        raise MessageCodecError("消息 payload 缺少有效的 data")

    try:
        message = messages_from_dict([
            {
                "type": message_type,
                "data": data,
            }
        ])[0]
    except (KeyError, TypeError, ValueError) as exc:
        raise MessageCodecError(
            f"无法恢复消息类型 {message_type!r}：{exc}"
        ) from exc

    event_type_for_message(message)
    return message
