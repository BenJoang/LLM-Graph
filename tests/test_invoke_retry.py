from __future__ import annotations

import httpx
import pytest
from openai import BadRequestError, InternalServerError

from src.context.invoke_retry import (
    ainvoke_with_retry,
    is_context_overflow_error,
)


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
    )


def test_context_overflow_accepts_mapping_body() -> None:
    error = BadRequestError(
        "context limit",
        response=_response(400),
        body={
            "message": "Maximum context length exceeded",
            "param": "input_tokens",
        },
    )

    assert is_context_overflow_error(error) is True


def test_non_mapping_error_body_is_not_treated_as_context_overflow() -> None:
    error = InternalServerError(
        "bad gateway",
        response=_response(502),
        body="<html><h1>502 Bad Gateway</h1></html>",
    )

    assert is_context_overflow_error(error) is False


@pytest.mark.asyncio
async def test_async_retry_preserves_internal_server_error() -> None:
    error = InternalServerError(
        "bad gateway",
        response=_response(502),
        body="<html><h1>502 Bad Gateway</h1></html>",
    )

    async def invoke(_messages):
        raise error

    async def compress(*_args):
        raise AssertionError("502 不应触发上下文压缩")

    with pytest.raises(InternalServerError) as exc_info:
        await ainvoke_with_retry(
            invoke_fn=invoke,
            messages=[],
            original_messages=[],
            compress_fn=compress,
        )

    assert exc_info.value is error
