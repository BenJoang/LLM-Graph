from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol, cast
from uuid import uuid4

import psycopg
from langchain_core.messages import BaseMessage
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.persistence.checkpoints import checkpoint_backend, postgres_url
from src.persistence.message_codec import (
    decode_json_value,
    deserialize_message,
    encode_json_value,
    event_type_for_message,
    serialize_message,
)


EventType = Literal[
    "user",
    "assistant",
    "tool",
    "system",
]

FinalRunStatus = Literal[
    "completed",
    "cancelled",
    "failed",
    "interrupted",
]

VALID_EVENT_TYPES = frozenset({
    "user",
    "assistant",
    "tool",
    "system",
})

VALID_FINAL_RUN_STATUSES = frozenset({
    "completed",
    "cancelled",
    "failed",
    "interrupted",
})

VALID_RUN_STATUSES = frozenset({
    "running",
    *VALID_FINAL_RUN_STATUSES,
})

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONVERSATION_SQLITE_PATH = (
    PROJECT_ROOT / "outputs" / "conversations" / "conversation.sqlite3"
)

class ActiveRunError(RuntimeError):
    """同一个会话已经存在正在运行的任务。"""


class RunNotFoundError(RuntimeError):
    """指定的 run 不存在。"""


class RunNotWritableError(RuntimeError):
    """run 当前状态不允许追加新事件。"""


class EventConflictError(RuntimeError):
    """同一个 message_id 被用于不同的消息内容。"""


class RunStateConflictError(RuntimeError):
    """run 已经结束，不能转换到另一个终态。"""


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    session_id: str
    turn_no: int
    status: str
    base_event_id: int
    checkpoint_thread_id: str | None
    heartbeat_at: datetime | None
    started_at: datetime
    finished_at: datetime | None
    error: str | None


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    session_id: str
    run_id: str
    turn_no: int
    ordinal: int
    message_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ContextRecord:
    session_id: str
    through_event_id: int
    version: int
    projected_messages: list[BaseMessage]
    compression_session: dict[str, Any]
    updated_at: datetime


def conversation_database_url() -> str:
    """返回对话业务数据库 URL。

    显式配置优先；未配置时跟随 checkpoint 后端，但 SQLite 使用
    独立文件，避免业务表和 LangGraph 内部表混在一起。
    """

    configured = os.getenv(
        "LLM_GRAPH_DATABASE_URL",
        "",
    ).strip()

    if configured:
        return configured

    if checkpoint_backend() == "sqlite":
        return (
            "sqlite:///"
            + DEFAULT_CONVERSATION_SQLITE_PATH.as_posix()
        )

    return postgres_url()


def _postgres_connection_url(
    database_url: str | None,
) -> str:
    connection_url = database_url or conversation_database_url()
    scheme = connection_url.partition(":")[0].lower()
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError(
            "PostgreSQL 专用函数需要 postgres/postgresql URL；"
            "需要自动选择后端时请使用 create_conversation_store()"
        )
    return connection_url


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
        session_id TEXT PRIMARY KEY,
        next_turn_no BIGINT NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_runs (
        run_id TEXT PRIMARY KEY,

        session_id TEXT NOT NULL
            REFERENCES conversation_sessions(session_id)
            ON DELETE CASCADE,

        turn_no BIGINT NOT NULL,

        status TEXT NOT NULL CHECK (
            status IN (
                'running',
                'completed',
                'cancelled',
                'interrupted',
                'failed'
            )
        ),

        base_event_id BIGINT NOT NULL DEFAULT 0,
        checkpoint_thread_id TEXT,

        heartbeat_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ,
        error TEXT,

        UNIQUE (session_id, turn_no),
        UNIQUE (session_id, run_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        uq_conversation_runs_active_session
    ON conversation_runs(session_id)
    WHERE status = 'running'
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_events (
        event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        turn_no BIGINT NOT NULL,
        ordinal INTEGER NOT NULL,

        message_id TEXT NOT NULL,

        event_type TEXT NOT NULL CHECK (
            event_type IN (
                'user',
                'assistant',
                'tool',
                'system'
            )
        ),

        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        FOREIGN KEY (session_id, run_id)
            REFERENCES conversation_runs(session_id, run_id)
            ON DELETE CASCADE,

        UNIQUE (run_id, ordinal),
        UNIQUE (run_id, message_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
        idx_conversation_events_session
    ON conversation_events(session_id, event_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_context (
        session_id TEXT PRIMARY KEY
            REFERENCES conversation_sessions(session_id)
            ON DELETE CASCADE,
        through_event_id BIGINT NOT NULL DEFAULT 0,
        version BIGINT NOT NULL DEFAULT 1,
        projected_messages JSONB NOT NULL DEFAULT '[]'::jsonb,
        compression_session JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_migrations (
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_version TEXT NOT NULL,
        checksum TEXT NOT NULL,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (source, source_id)
    )
    """,
)


def _clean_identifier(
    value: str,
    *,
    field_name: str,
) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError(f"{field_name} 不能为空")

    if len(cleaned) > 255:
        raise ValueError(f"{field_name} 长度不能超过 255")

    return cleaned


def _clean_event_type(value: str) -> EventType:
    cleaned = value.strip().lower()

    if cleaned not in VALID_EVENT_TYPES:
        raise ValueError(
            "event_type 必须是："
            + ", ".join(sorted(VALID_EVENT_TYPES))
        )

    return cast(EventType, cleaned)


def _clean_final_run_status(value: str) -> FinalRunStatus:
    cleaned = value.strip().lower()

    if cleaned not in VALID_FINAL_RUN_STATUSES:
        raise ValueError(
            "最终 run 状态必须是："
            + ", ".join(sorted(VALID_FINAL_RUN_STATUSES))
        )

    return cast(FinalRunStatus, cleaned)


def _clean_run_statuses(statuses: Iterable[str]) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(
        status.strip().lower()
        for status in statuses
    ))

    if not cleaned:
        raise ValueError("statuses 不能为空")

    invalid = set(cleaned) - VALID_RUN_STATUSES
    if invalid:
        raise ValueError(
            "未知 run 状态："
            + ", ".join(sorted(invalid))
        )

    return cleaned


def _message_identifier(
    message: BaseMessage,
    explicit_message_id: str | None,
) -> str:
    if (
        explicit_message_id is not None
        and message.id is not None
        and str(message.id).strip() != explicit_message_id.strip()
    ):
        raise ValueError(
            "显式 message_id 与 message.id 不一致"
        )

    message_id = explicit_message_id or message.id
    if message_id is None:
        raise ValueError(
            "消息没有 id；请传入稳定的 message_id，"
            "以保证事件写入可以幂等重试"
        )

    return _clean_identifier(
        str(message_id),
        field_name="message_id",
    )


def setup_conversation_store(
    database_url: str | None = None,
) -> None:
    """创建对话日志需要的业务表。

    此函数可以重复执行；不应在模块 import 时自动执行。
    """

    connection_url = _postgres_connection_url(database_url)

    with psycopg.connect(connection_url) as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)


def ensure_session(
    session_id: str,
    *,
    database_url: str | None = None,
) -> None:
    cleaned_session_id = _clean_identifier(
        session_id,
        field_name="session_id",
    )
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(connection_url) as connection:
        connection.execute(
            """
            INSERT INTO conversation_sessions (session_id)
            VALUES (%s)
            ON CONFLICT (session_id) DO NOTHING
            """,
            (cleaned_session_id,),
        )


def get_run(
    run_id: str,
    *,
    database_url: str | None = None,
) -> RunRecord | None:
    cleaned_run_id = _clean_identifier(run_id, field_name="run_id")
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            """
            SELECT
                run_id, session_id, turn_no, status, base_event_id,
                checkpoint_thread_id, heartbeat_at, started_at,
                finished_at, error
            FROM conversation_runs
            WHERE run_id = %s
            """,
            (cleaned_run_id,),
        ).fetchone()
    return RunRecord(**dict(row)) if row else None


def delete_session(
    session_id: str,
    *,
    database_url: str | None = None,
) -> bool:
    cleaned_session_id = _clean_identifier(
        session_id,
        field_name="session_id",
    )
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(connection_url) as connection:
        cursor = connection.execute(
            "DELETE FROM conversation_sessions WHERE session_id = %s",
            (cleaned_session_id,),
        )
    return cursor.rowcount > 0


def interrupt_running_runs(
    *,
    reason: str = "process restarted",
    database_url: str | None = None,
) -> int:
    cleaned_reason = reason.strip() or "process restarted"
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(connection_url) as connection:
        cursor = connection.execute(
            """
            UPDATE conversation_runs
            SET status = 'interrupted',
                finished_at = NOW(),
                error = %s
            WHERE status = 'running'
            """,
            (cleaned_reason,),
        )
    return cursor.rowcount

def begin_run(
    session_id: str,
    *,
    run_id: str | None = None,
    database_url: str | None = None,
) -> RunRecord:
    """原子创建一次新的会话运行。

    同一个 session 同时只允许一个 running run。
    turn_no 在数据库事务内分配，避免并发重复。
    """

    cleaned_session_id = _clean_identifier(
        session_id,
        field_name="session_id",
    )

    cleaned_run_id = _clean_identifier(
        run_id or f"run-{uuid4().hex}",
        field_name="run_id",
    )

    connection_url = _postgres_connection_url(database_url)

    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            # session 不存在时创建；已经存在时不修改。
            cursor.execute(
                """
                INSERT INTO conversation_sessions (session_id)
                VALUES (%s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (cleaned_session_id,),
            )

            # 锁定当前 session 行。
            # 其他并发 begin_run 必须等待当前事务完成。
            cursor.execute(
                """
                SELECT next_turn_no
                FROM conversation_sessions
                WHERE session_id = %s
                FOR UPDATE
                """,
                (cleaned_session_id,),
            )

            session_row = cursor.fetchone()

            if session_row is None:
                raise RuntimeError("创建或读取 conversation session 失败")

            # 在锁内检查是否已有活动运行。
            cursor.execute(
                """
                SELECT run_id
                FROM conversation_runs
                WHERE session_id = %s
                  AND status = 'running'
                LIMIT 1
                """,
                (cleaned_session_id,),
            )

            active_row = cursor.fetchone()

            if active_row is not None:
                raise ActiveRunError(
                    "当前会话已经存在正在运行的任务："
                    f"{active_row['run_id']}"
                )

            turn_no = int(session_row["next_turn_no"])

            # 记录本轮开始前数据库中已有事件的最高位置。
            cursor.execute(
                """
                SELECT COALESCE(MAX(event_id), 0) AS base_event_id
                FROM conversation_events
                WHERE session_id = %s
                """,
                (cleaned_session_id,),
            )

            event_row = cursor.fetchone()
            base_event_id = int(event_row["base_event_id"])

            cursor.execute(
                """
                INSERT INTO conversation_runs (
                    run_id,
                    session_id,
                    turn_no,
                    status,
                    base_event_id
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'running',
                    %s
                )
                RETURNING
                    run_id,
                    session_id,
                    turn_no,
                    status,
                    base_event_id,
                    checkpoint_thread_id,
                    heartbeat_at,
                    started_at,
                    finished_at,
                    error
                """,
                (
                    cleaned_run_id,
                    cleaned_session_id,
                    turn_no,
                    base_event_id,
                ),
            )

            run_row = cursor.fetchone()

            if run_row is None:
                raise RuntimeError("创建 conversation run 失败")

            cursor.execute(
                """
                UPDATE conversation_sessions
                SET
                    next_turn_no = next_turn_no + 1,
                    updated_at = NOW()
                WHERE session_id = %s
                """,
                (cleaned_session_id,),
            )

    return RunRecord(**dict(run_row))


def append_event(
    run_id: str,
    *,
    message_id: str,
    event_type: EventType,
    payload: dict[str, Any],
    database_url: str | None = None,
) -> EventRecord:
    """向一个 running run 幂等追加消息事件。

    相同 message_id 和内容的重复写入返回原事件；相同 message_id
    对应不同内容时抛出 EventConflictError。
    """

    cleaned_run_id = _clean_identifier(
        run_id,
        field_name="run_id",
    )
    cleaned_message_id = _clean_identifier(
        message_id,
        field_name="message_id",
    )
    cleaned_event_type = _clean_event_type(event_type)

    if not isinstance(payload, dict):
        raise TypeError("payload 必须是 dict")

    connection_url = _postgres_connection_url(database_url)

    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            # Serialize ordinal allocation for all writers of this run.
            cursor.execute(
                """
                SELECT
                    run_id,
                    session_id,
                    turn_no,
                    status
                FROM conversation_runs
                WHERE run_id = %s
                FOR UPDATE
                """,
                (cleaned_run_id,),
            )
            run_row = cursor.fetchone()

            if run_row is None:
                raise RunNotFoundError(
                    f"运行不存在：{cleaned_run_id}"
                )

            # Handle idempotent retries before checking the current run
            # status. An already committed event can therefore be safely
            # retried after its run has completed.
            cursor.execute(
                """
                SELECT
                    event_id,
                    session_id,
                    run_id,
                    turn_no,
                    ordinal,
                    message_id,
                    event_type,
                    payload,
                    created_at
                FROM conversation_events
                WHERE run_id = %s
                  AND message_id = %s
                """,
                (
                    cleaned_run_id,
                    cleaned_message_id,
                ),
            )
            existing_row = cursor.fetchone()

            if existing_row is not None:
                existing = EventRecord(**dict(existing_row))
                same_content = (
                    existing.event_type == cleaned_event_type
                    and existing.payload == payload
                )

                if not same_content:
                    raise EventConflictError(
                        "message_id 已经存在，但事件类型或内容不同："
                        f"{cleaned_message_id}"
                    )

                return existing

            if run_row["status"] != "running":
                raise RunNotWritableError(
                    "只有 running 状态的 run 可以追加新事件；"
                    f"当前状态：{run_row['status']}"
                )

            cursor.execute(
                """
                SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                FROM conversation_events
                WHERE run_id = %s
                """,
                (cleaned_run_id,),
            )
            ordinal_row = cursor.fetchone()
            next_ordinal = int(ordinal_row["next_ordinal"])

            cursor.execute(
                """
                INSERT INTO conversation_events (
                    session_id,
                    run_id,
                    turn_no,
                    ordinal,
                    message_id,
                    event_type,
                    payload
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING
                    event_id,
                    session_id,
                    run_id,
                    turn_no,
                    ordinal,
                    message_id,
                    event_type,
                    payload,
                    created_at
                """,
                (
                    run_row["session_id"],
                    cleaned_run_id,
                    run_row["turn_no"],
                    next_ordinal,
                    cleaned_message_id,
                    cleaned_event_type,
                    Jsonb(payload),
                ),
            )
            event_row = cursor.fetchone()

            if event_row is None:
                raise RuntimeError("创建 conversation event 失败")

    return EventRecord(**dict(event_row))


def append_message(
    run_id: str,
    message: BaseMessage,
    *,
    message_id: str | None = None,
    database_url: str | None = None,
) -> EventRecord:
    """序列化并幂等追加一条完整 LangChain 消息。"""

    resolved_message_id = _message_identifier(
        message,
        message_id,
    )
    return append_event(
        run_id,
        message_id=resolved_message_id,
        event_type=cast(
            EventType,
            event_type_for_message(message),
        ),
        payload=serialize_message(message),
        database_url=database_url,
    )


def list_events(
    session_id: str,
    *,
    statuses: Iterable[str] | None = None,
    database_url: str | None = None,
) -> list[EventRecord]:
    """按轮次和轮内顺序读取会话事件。"""

    cleaned_session_id = _clean_identifier(
        session_id,
        field_name="session_id",
    )
    cleaned_statuses = (
        _clean_run_statuses(statuses)
        if statuses is not None
        else None
    )
    connection_url = _postgres_connection_url(database_url)

    query = """
        SELECT
            event.event_id,
            event.session_id,
            event.run_id,
            event.turn_no,
            event.ordinal,
            event.message_id,
            event.event_type,
            event.payload,
            event.created_at
        FROM conversation_events AS event
        JOIN conversation_runs AS run
          ON run.run_id = event.run_id
        WHERE event.session_id = %s
    """
    parameters: tuple[Any, ...] = (cleaned_session_id,)
    if cleaned_statuses is not None:
        query += " AND run.status = ANY(%s)"
        parameters += (list(cleaned_statuses),)
    query += " ORDER BY event.turn_no, event.ordinal"

    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()

    return [EventRecord(**dict(row)) for row in rows]


def load_messages(
    session_id: str,
    *,
    statuses: Iterable[str] = ("completed",),
    database_url: str | None = None,
) -> list[BaseMessage]:
    """从指定状态的事件恢复 LangChain 消息。

    默认只读取 completed run，避免取消、失败或中断产生的残缺消息
    进入下一轮正式模型上下文。
    """

    events = list_events(
        session_id,
        statuses=statuses,
        database_url=database_url,
    )
    messages: list[BaseMessage] = []
    for event in events:
        message = deserialize_message(event.payload)
        actual_event_type = event_type_for_message(message)
        if actual_event_type != event.event_type:
            raise EventConflictError(
                "事件类型与消息 payload 不一致："
                f"event_id={event.event_id}，"
                f"event_type={event.event_type}，"
                f"payload_type={actual_event_type}"
            )
        messages.append(message)

    return messages


def load_context(
    session_id: str,
    *,
    database_url: str | None = None,
) -> ContextRecord | None:
    cleaned_session_id = _clean_identifier(
        session_id,
        field_name="session_id",
    )
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            """
            SELECT
                session_id,
                through_event_id,
                version,
                projected_messages,
                compression_session,
                updated_at
            FROM conversation_context
            WHERE session_id = %s
            """,
            (cleaned_session_id,),
        ).fetchone()

    if row is None:
        return None
    payloads = row["projected_messages"]
    if not isinstance(payloads, list):
        raise ValueError("conversation_context.projected_messages 必须是数组")
    compression = row["compression_session"]
    if not isinstance(compression, dict):
        raise ValueError("conversation_context.compression_session 必须是对象")
    return ContextRecord(
        session_id=row["session_id"],
        through_event_id=int(row["through_event_id"]),
        version=int(row["version"]),
        projected_messages=[
            deserialize_message(payload) for payload in payloads
        ],
        compression_session=decode_json_value(compression),
        updated_at=row["updated_at"],
    )


def commit_run(
    run_id: str,
    *,
    projected_messages: Iterable[BaseMessage],
    compression_session: dict[str, Any],
    database_url: str | None = None,
) -> RunRecord:
    """原子提交 completed Run 和最新模型上下文投影。"""

    cleaned_run_id = _clean_identifier(run_id, field_name="run_id")
    if not isinstance(compression_session, dict):
        raise TypeError("compression_session 必须是 dict")
    projected_payloads = [
        serialize_message(message) for message in projected_messages
    ]
    connection_url = _postgres_connection_url(database_url)

    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        run_row = connection.execute(
            """
            SELECT
                run_id, session_id, turn_no, status, base_event_id,
                checkpoint_thread_id, heartbeat_at, started_at,
                finished_at, error
            FROM conversation_runs
            WHERE run_id = %s
            FOR UPDATE
            """,
            (cleaned_run_id,),
        ).fetchone()
        if run_row is None:
            raise RunNotFoundError(f"运行不存在：{cleaned_run_id}")

        current = RunRecord(**dict(run_row))
        if current.status == "completed":
            return current
        if current.status != "running":
            raise RunStateConflictError(
                "只有 running run 可以提交上下文；"
                f"当前状态：{current.status}"
            )

        event_row = connection.execute(
            """
            SELECT COALESCE(MAX(event_id), 0) AS through_event_id
            FROM conversation_events
            WHERE session_id = %s
            """,
            (current.session_id,),
        ).fetchone()
        through_event_id = int(event_row["through_event_id"])

        finished_row = connection.execute(
            """
            UPDATE conversation_runs
            SET status = 'completed', finished_at = NOW(), error = NULL
            WHERE run_id = %s
            RETURNING
                run_id, session_id, turn_no, status, base_event_id,
                checkpoint_thread_id, heartbeat_at, started_at,
                finished_at, error
            """,
            (cleaned_run_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO conversation_context (
                session_id,
                through_event_id,
                version,
                projected_messages,
                compression_session,
                updated_at
            ) VALUES (%s, %s, 1, %s, %s, NOW())
            ON CONFLICT (session_id) DO UPDATE
            SET through_event_id = EXCLUDED.through_event_id,
                version = conversation_context.version + 1,
                projected_messages = EXCLUDED.projected_messages,
                compression_session = EXCLUDED.compression_session,
                updated_at = NOW()
            """,
            (
                current.session_id,
                through_event_id,
                Jsonb(projected_payloads),
                Jsonb(encode_json_value(compression_session)),
            ),
        )
        connection.execute(
            """
            UPDATE conversation_sessions
            SET updated_at = NOW()
            WHERE session_id = %s
            """,
            (current.session_id,),
        )

    if finished_row is None:
        raise RuntimeError("提交 conversation run 失败")
    return RunRecord(**dict(finished_row))


def replace_context(
    session_id: str,
    *,
    projected_messages: Iterable[BaseMessage],
    compression_session: dict[str, Any],
    database_url: str | None = None,
) -> ContextRecord:
    """重建指定会话的模型投影，供显式迁移和修复命令使用。"""

    cleaned_session_id = _clean_identifier(
        session_id,
        field_name="session_id",
    )
    if not isinstance(compression_session, dict):
        raise TypeError("compression_session 必须是 dict")
    payloads = [serialize_message(message) for message in projected_messages]
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        session_row = connection.execute(
            """
            SELECT session_id FROM conversation_sessions
            WHERE session_id = %s FOR UPDATE
            """,
            (cleaned_session_id,),
        ).fetchone()
        if session_row is None:
            raise ValueError(f"会话不存在：{cleaned_session_id}")
        event_row = connection.execute(
            """
            SELECT COALESCE(MAX(event_id), 0) AS through_event_id
            FROM conversation_events WHERE session_id = %s
            """,
            (cleaned_session_id,),
        ).fetchone()
        through_event_id = int(event_row["through_event_id"])
        row = connection.execute(
            """
            INSERT INTO conversation_context (
                session_id, through_event_id, version,
                projected_messages, compression_session, updated_at
            ) VALUES (%s, %s, 1, %s, %s, NOW())
            ON CONFLICT (session_id) DO UPDATE SET
                through_event_id = EXCLUDED.through_event_id,
                version = conversation_context.version + 1,
                projected_messages = EXCLUDED.projected_messages,
                compression_session = EXCLUDED.compression_session,
                updated_at = NOW()
            RETURNING session_id, through_event_id, version,
                projected_messages, compression_session, updated_at
            """,
            (
                cleaned_session_id,
                through_event_id,
                Jsonb(payloads),
                Jsonb(encode_json_value(compression_session)),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("重建 conversation context 失败")
    return ContextRecord(
        session_id=row["session_id"],
        through_event_id=int(row["through_event_id"]),
        version=int(row["version"]),
        projected_messages=[
            deserialize_message(payload)
            for payload in row["projected_messages"]
        ],
        compression_session=decode_json_value(row["compression_session"]),
        updated_at=row["updated_at"],
    )


def get_migration(
    source: str,
    source_id: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            """
            SELECT source, source_id, source_version, checksum,
                   details, migrated_at
            FROM conversation_migrations
            WHERE source = %s AND source_id = %s
            """,
            (source, source_id),
        ).fetchone()
    return dict(row) if row else None


def record_migration(
    source: str,
    source_id: str,
    *,
    source_version: str,
    checksum: str,
    details: dict[str, Any],
    database_url: str | None = None,
) -> None:
    connection_url = _postgres_connection_url(database_url)
    with psycopg.connect(connection_url) as connection:
        row = connection.execute(
            """
            INSERT INTO conversation_migrations (
                source, source_id, source_version, checksum,
                details, migrated_at
            ) VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (source, source_id) DO UPDATE SET
                source_version = EXCLUDED.source_version,
                checksum = EXCLUDED.checksum,
                details = EXCLUDED.details,
                migrated_at = NOW()
            WHERE conversation_migrations.checksum = EXCLUDED.checksum
            RETURNING checksum
            """,
            (
                source,
                source_id,
                source_version,
                checksum,
                Jsonb(details),
            ),
        ).fetchone()
        if row is None:
            raise EventConflictError(
                f"迁移来源内容已改变：{source}/{source_id}"
            )


def finish_run(
    run_id: str,
    *,
    status: FinalRunStatus,
    error: str | None = None,
    database_url: str | None = None,
) -> RunRecord:
    """将 running run 原子转换为一个最终状态。"""

    cleaned_run_id = _clean_identifier(
        run_id,
        field_name="run_id",
    )
    cleaned_status = _clean_final_run_status(status)
    cleaned_error = error.strip() if error else None

    if cleaned_status == "failed" and not cleaned_error:
        raise ValueError("failed run 必须提供 error")

    connection_url = _postgres_connection_url(database_url)

    with psycopg.connect(
        connection_url,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    run_id,
                    session_id,
                    turn_no,
                    status,
                    base_event_id,
                    checkpoint_thread_id,
                    heartbeat_at,
                    started_at,
                    finished_at,
                    error
                FROM conversation_runs
                WHERE run_id = %s
                FOR UPDATE
                """,
                (cleaned_run_id,),
            )
            run_row = cursor.fetchone()

            if run_row is None:
                raise RunNotFoundError(
                    f"运行不存在：{cleaned_run_id}"
                )

            current = RunRecord(**dict(run_row))

            # Retrying an already-applied terminal transition is idempotent.
            if current.status == cleaned_status:
                return current

            if current.status != "running":
                raise RunStateConflictError(
                    "已经结束的 run 不能转换到另一个状态；"
                    f"当前状态：{current.status}，"
                    f"目标状态：{cleaned_status}"
                )

            cursor.execute(
                """
                UPDATE conversation_runs
                SET
                    status = %s,
                    finished_at = NOW(),
                    error = %s
                WHERE run_id = %s
                RETURNING
                    run_id,
                    session_id,
                    turn_no,
                    status,
                    base_event_id,
                    checkpoint_thread_id,
                    heartbeat_at,
                    started_at,
                    finished_at,
                    error
                """,
                (
                    cleaned_status,
                    cleaned_error,
                    cleaned_run_id,
                ),
            )
            finished_row = cursor.fetchone()

            if finished_row is None:
                raise RuntimeError("结束 conversation run 失败")

            cursor.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = NOW()
                WHERE session_id = %s
                """,
                (current.session_id,),
            )

    return RunRecord(**dict(finished_row))


def complete_run(
    run_id: str,
    *,
    database_url: str | None = None,
) -> RunRecord:
    return finish_run(
        run_id,
        status="completed",
        database_url=database_url,
    )


def cancel_run(
    run_id: str,
    *,
    reason: str | None = None,
    database_url: str | None = None,
) -> RunRecord:
    return finish_run(
        run_id,
        status="cancelled",
        error=reason,
        database_url=database_url,
    )


def fail_run(
    run_id: str,
    *,
    error: str,
    database_url: str | None = None,
) -> RunRecord:
    return finish_run(
        run_id,
        status="failed",
        error=error,
        database_url=database_url,
    )


def interrupt_run(
    run_id: str,
    *,
    reason: str | None = None,
    database_url: str | None = None,
) -> RunRecord:
    return finish_run(
        run_id,
        status="interrupted",
        error=reason,
        database_url=database_url,
    )


class ConversationStore(Protocol):
    """PostgreSQL 和 SQLite 共同遵守的最小存储契约。"""

    database_url: str

    def setup(self) -> None: ...

    def close(self) -> None: ...

    def ensure_session(self, session_id: str) -> None: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def delete_session(self, session_id: str) -> bool: ...

    def interrupt_running_runs(
        self,
        *,
        reason: str = "process restarted",
    ) -> int: ...

    def begin_run(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> RunRecord: ...

    def append_event(
        self,
        run_id: str,
        *,
        message_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> EventRecord: ...

    def append_message(
        self,
        run_id: str,
        message: BaseMessage,
        *,
        message_id: str | None = None,
    ) -> EventRecord: ...

    def list_events(
        self,
        session_id: str,
        *,
        statuses: Iterable[str] | None = None,
    ) -> list[EventRecord]: ...

    def load_messages(
        self,
        session_id: str,
        *,
        statuses: Iterable[str] = ("completed",),
    ) -> list[BaseMessage]: ...

    def load_context(
        self,
        session_id: str,
    ) -> ContextRecord | None: ...

    def commit_run(
        self,
        run_id: str,
        *,
        projected_messages: Iterable[BaseMessage],
        compression_session: dict[str, Any],
    ) -> RunRecord: ...

    def replace_context(
        self,
        session_id: str,
        *,
        projected_messages: Iterable[BaseMessage],
        compression_session: dict[str, Any],
    ) -> ContextRecord: ...

    def get_migration(
        self,
        source: str,
        source_id: str,
    ) -> dict[str, Any] | None: ...

    def record_migration(
        self,
        source: str,
        source_id: str,
        *,
        source_version: str,
        checksum: str,
        details: dict[str, Any],
    ) -> None: ...

    def finish_run(
        self,
        run_id: str,
        *,
        status: FinalRunStatus,
        error: str | None = None,
    ) -> RunRecord: ...

    def complete_run(self, run_id: str) -> RunRecord: ...

    def cancel_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> RunRecord: ...

    def fail_run(
        self,
        run_id: str,
        *,
        error: str,
    ) -> RunRecord: ...

    def interrupt_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> RunRecord: ...


class PostgresConversationStore:
    """保留现有函数 API 的 PostgreSQL 对象适配器。"""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = _postgres_connection_url(database_url)

    def setup(self) -> None:
        setup_conversation_store(self.database_url)

    def close(self) -> None:
        """PostgreSQL 实现按调用打开连接，无常驻资源。"""

    def ensure_session(self, session_id: str) -> None:
        ensure_session(session_id, database_url=self.database_url)

    def get_run(self, run_id: str) -> RunRecord | None:
        return get_run(run_id, database_url=self.database_url)

    def delete_session(self, session_id: str) -> bool:
        return delete_session(session_id, database_url=self.database_url)

    def interrupt_running_runs(
        self,
        *,
        reason: str = "process restarted",
    ) -> int:
        return interrupt_running_runs(
            reason=reason,
            database_url=self.database_url,
        )

    def begin_run(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> RunRecord:
        return begin_run(
            session_id,
            run_id=run_id,
            database_url=self.database_url,
        )

    def append_event(
        self,
        run_id: str,
        *,
        message_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> EventRecord:
        return append_event(
            run_id,
            message_id=message_id,
            event_type=event_type,
            payload=payload,
            database_url=self.database_url,
        )

    def append_message(
        self,
        run_id: str,
        message: BaseMessage,
        *,
        message_id: str | None = None,
    ) -> EventRecord:
        return append_message(
            run_id,
            message,
            message_id=message_id,
            database_url=self.database_url,
        )

    def list_events(
        self,
        session_id: str,
        *,
        statuses: Iterable[str] | None = None,
    ) -> list[EventRecord]:
        return list_events(
            session_id,
            statuses=statuses,
            database_url=self.database_url,
        )

    def load_messages(
        self,
        session_id: str,
        *,
        statuses: Iterable[str] = ("completed",),
    ) -> list[BaseMessage]:
        return load_messages(
            session_id,
            statuses=statuses,
            database_url=self.database_url,
        )

    def load_context(
        self,
        session_id: str,
    ) -> ContextRecord | None:
        return load_context(
            session_id,
            database_url=self.database_url,
        )

    def commit_run(
        self,
        run_id: str,
        *,
        projected_messages: Iterable[BaseMessage],
        compression_session: dict[str, Any],
    ) -> RunRecord:
        return commit_run(
            run_id,
            projected_messages=projected_messages,
            compression_session=compression_session,
            database_url=self.database_url,
        )

    def replace_context(
        self,
        session_id: str,
        *,
        projected_messages: Iterable[BaseMessage],
        compression_session: dict[str, Any],
    ) -> ContextRecord:
        return replace_context(
            session_id,
            projected_messages=projected_messages,
            compression_session=compression_session,
            database_url=self.database_url,
        )

    def get_migration(
        self,
        source: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        return get_migration(
            source,
            source_id,
            database_url=self.database_url,
        )

    def record_migration(
        self,
        source: str,
        source_id: str,
        *,
        source_version: str,
        checksum: str,
        details: dict[str, Any],
    ) -> None:
        record_migration(
            source,
            source_id,
            source_version=source_version,
            checksum=checksum,
            details=details,
            database_url=self.database_url,
        )

    def finish_run(
        self,
        run_id: str,
        *,
        status: FinalRunStatus,
        error: str | None = None,
    ) -> RunRecord:
        return finish_run(
            run_id,
            status=status,
            error=error,
            database_url=self.database_url,
        )

    def complete_run(self, run_id: str) -> RunRecord:
        return complete_run(
            run_id,
            database_url=self.database_url,
        )

    def cancel_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> RunRecord:
        return cancel_run(
            run_id,
            reason=reason,
            database_url=self.database_url,
        )

    def fail_run(
        self,
        run_id: str,
        *,
        error: str,
    ) -> RunRecord:
        return fail_run(
            run_id,
            error=error,
            database_url=self.database_url,
        )

    def interrupt_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> RunRecord:
        return interrupt_run(
            run_id,
            reason=reason,
            database_url=self.database_url,
        )


def create_conversation_store(
    database_url: str | None = None,
) -> ConversationStore:
    """根据数据库 URL 创建对应的对话日志实现。"""

    connection_url = database_url or conversation_database_url()
    scheme = connection_url.partition(":")[0].lower()

    if scheme == "sqlite":
        from src.persistence.sqlite_conversation_store import (
            SQLiteConversationStore,
        )

        return SQLiteConversationStore(connection_url)
    if scheme in {"postgres", "postgresql"}:
        return PostgresConversationStore(connection_url)

    raise ValueError(
        "不支持的 conversation database URL："
        f"{connection_url!r}"
    )
