from typing import Any
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage
from langgraph.prebuilt import ToolNode
from langchain_core.runnables import RunnableConfig

CONTEXT_STATE_KEY = "personal_context_state"

def get_context_state(msg: BaseMessage) -> dict:
    metadata = getattr(msg, "response_metadata", None) or {}
    return metadata.get(CONTEXT_STATE_KEY, {}) or {}

def set_context_state(msg: BaseMessage, **patch: Any) -> BaseMessage:
    metadata = getattr(msg, "response_metadata", None) or {}
    context_state = dict(metadata.get(CONTEXT_STATE_KEY, {}) or {})
    context_state.update(patch)

    metadata[CONTEXT_STATE_KEY] = context_state
    msg.response_metadata = metadata
    return msg

def get_message_turn_id(msg: BaseMessage) -> int | None:
    context_state = get_context_state(msg)
    turn_id = context_state.get("turn_id")

    if isinstance(turn_id, int):
        return turn_id

    if isinstance(turn_id, str) and turn_id.isdigit():
        return int(turn_id)

    return None

def get_next_turn_id(messages: list[BaseMessage]) -> int:
    max_turn_id = 0

    for msg in messages or []:
        if not isinstance(msg, HumanMessage):
            continue

        turn_id = get_message_turn_id(msg)
        if turn_id is None:
            continue

        max_turn_id = max(max_turn_id, turn_id)

    return max_turn_id + 1


def make_initial_state(
    question: str,
    *,
    turn_id: int,
) -> dict:
    message = HumanMessage(content=question)

    set_context_state(
        message,
        turn_id=turn_id,
    )

    return {
        "messages": [message],
        "turn_id": turn_id
    }

def mark_ai_message(
    message: AIMessage,
    *,
    turn_id: int,
) -> AIMessage:
    set_context_state(
        message,
        turn_id=turn_id,
    )

    return message

def mark_tool_message(
    message: ToolMessage,
    *,
    turn_id: int,
) -> ToolMessage:
    set_context_state(
        message,
        turn_id=turn_id,
    )
    return message

def build_turn_aware_tool_node(
        tools,
        messages_key: str = "messages",
    ):
    raw_tool_node = ToolNode(
        tools,
        messages_key=messages_key,
    )

    def tools_node(
            state: dict,
            config: RunnableConfig,
            ) -> dict:
        result = raw_tool_node.invoke(
            state,
            config=config
        )

        messages = result.get(messages_key, [])

        for message in messages:
            if isinstance(message, ToolMessage):
                mark_tool_message(
                    message,
                    turn_id=state["turn_id"],
                )

        return {
            **result,
            messages_key: messages,
        }

    return tools_node