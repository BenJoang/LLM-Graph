from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from src.graphs.tool_agent_graph import CHECKPOINT_DB


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, default=str)


def message_to_dto(message: Any) -> dict:
    if isinstance(message, dict):
        role = str(message.get("role", message.get("type", "unknown")))
        return {
            "id": str(message.get("id") or ""),
            "role": role,
            "content": _text_content(message.get("content")),
            "tool_calls": message.get("tool_calls") or [],
            "tool_call_id": message.get("tool_call_id"),
            "name": message.get("name"),
            "status": message.get("status"),
        }

    class_name = message.__class__.__name__
    role = {
        "HumanMessage": "user",
        "AIMessage": "assistant",
        "ToolMessage": "tool",
        "SystemMessage": "system",
    }.get(class_name, class_name)
    return {
        "id": str(getattr(message, "id", None) or ""),
        "role": role,
        "content": _text_content(getattr(message, "content", "")),
        "tool_calls": getattr(message, "tool_calls", None) or [],
        "tool_call_id": getattr(message, "tool_call_id", None),
        "name": getattr(message, "name", None),
        "status": getattr(message, "status", None),
    }


def read_thread_messages(
    thread_id: str,
    checkpoint_db: str | Path = CHECKPOINT_DB,
) -> list[dict]:
    path = Path(checkpoint_db)
    if not path.exists():
        return []
    with SqliteSaver.from_conn_string(str(path)) as saver:
        item = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    if item is None:
        return []
    values = item.checkpoint.get("channel_values", {})
    return [message_to_dto(message) for message in values.get("messages", [])]
