from openai import BadRequestError
from collections.abc import Callable
from langchain_core.messages import AIMessage

from src.context.message_context import mark_ai_message


def is_context_overflow_error(e: Exception) -> bool:
    body = getattr(e, "body", {}) or {}
    message = str(body.get("message", "")) or str(e)

    return (
        isinstance(e, BadRequestError)
        and getattr(e, "status_code", None) == 400
        and (
            body.get("param") == "input_tokens"
            or "maximum context length" in message
            or "context length" in message
        )
    )



def invoke_with_retry(
    invoke_fn: Callable,
    messages: list,
    original_messages: list,
    compress_fn: Callable[[list, int], list],
    *,
    turn_id: int | None = None
):
    current_messages = messages
    last_error = None

    for retry_level in range(4):
        try:
            response =  invoke_fn(current_messages)

            if turn_id is not None and isinstance(response, AIMessage):
                response = mark_ai_message(response, turn_id=turn_id)

            return response

        except Exception as e:
            if not is_context_overflow_error(e):
                raise

            last_error = e

            if retry_level >= 3:
                break

            current_messages = compress_fn(current_messages, original_messages, retry_level + 1)
        
        

    raise RuntimeError("模型上下文超限，压缩重试后仍失败") from last_error