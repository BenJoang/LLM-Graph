import time
from openai import APITimeoutError, BadRequestError
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
    compress_fn: Callable[[list, list, int], list],
    *,
    turn_id: int | None = None,
    max_timeout_retries: int = 5,
    max_context_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
):
    current_messages = messages

    timeout_retries = 0
    context_retries = 0

    while True:
        try:
            response =  invoke_fn(current_messages)

            if turn_id is not None and isinstance(response, AIMessage):
                response = mark_ai_message(response, turn_id=turn_id)

            return response
        except APITimeoutError as e:
            if timeout_retries >= max_timeout_retries:
                raise RuntimeError(
                    f"模型调用超时，重试 {max_timeout_retries} 次后仍失败"
                ) from e

            # 依次等待 1、2、4、8、16 秒
            delay = min(
                max_delay,
                base_delay * (2 ** timeout_retries),
            )

            timeout_retries += 1
            time.sleep(delay)

        except Exception as e:
            if not is_context_overflow_error(e):
                raise

            if context_retries >= max_context_retries:
                raise RuntimeError(
                    f"模型上下文超限，压缩重试 "
                    f"{max_context_retries} 次后仍失败"
                ) from e

            context_retries += 1

            current_messages = compress_fn(current_messages, original_messages, context_retries)
        