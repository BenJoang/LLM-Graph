from __future__ import annotations

from src.context.context_compression import (
    CompressionSession,
    MessageManage,
)


class CompressionRetryAdapter:
    def __init__(
        self,
        message_manage: MessageManage,
        compression_session: CompressionSession | None,
        current_turn_id: int,
    ):
        self.message_manage = message_manage
        self.compression_session = (
            compression_session
        )
        self.current_turn_id = current_turn_id

        self.last_level: int | None = None
        self.last_changed = False
        self.last_estimated_tokens: int | None = None

    def __call__(
        self,
        current_messages: list,
        original_messages: list,
        level: int,
    ) -> list:
        (
            retry_messages,
            updated_session,
            changed,
        ) = self.message_manage.compress_for_retry(
            messages=current_messages,
            original_messages=original_messages,
            level=level,
            compression_session=(
                self.compression_session
            ),
            current_turn_id=self.current_turn_id,
        )

        self.compression_session = (
            updated_session
        )
        self.last_level = level
        self.last_changed = changed
        self.last_estimated_tokens = (
            self.message_manage.estimate_tokens(
                retry_messages
            )
        )

        return retry_messages