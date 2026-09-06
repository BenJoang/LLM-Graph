from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, cast
from urllib.parse import unquote
from uuid import uuid4

from langchain_core.messages import BaseMessage

from src.persistence.conversation_store import (
    ActiveRunError,
    EventConflictError,
    EventRecord,
    EventType,
    FinalRunStatus,
    ContextRecord,
    RunNotFoundError,
    RunNotWritableError,
    RunRecord,
    RunStateConflictError,
    _clean_event_type,
    _clean_final_run_status,
    _clean_identifier,
    _clean_run_statuses,
    _message_identifier,
)
from src.persistence.message_codec import (
    decode_json_value,
    deserialize_message,
    encode_json_value,
    event_type_for_message,
    serialize_message,
)


SQLITE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
        session_id TEXT PRIMARY KEY,
        next_turn_no INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES conversation_sessions(session_id)
            ON DELETE CASCADE,
        turn_no INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN (
                'running',
                'completed',
                'cancelled',
                'interrupted',
                'failed'
            )
        ),
        base_event_id INTEGER NOT NULL DEFAULT 0,
        checkpoint_thread_id TEXT,
        heartbeat_at TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
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
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        turn_no INTEGER NOT NULL,
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
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
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
        through_event_id INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1,
        projected_messages TEXT NOT NULL DEFAULT '[]',
        compression_session TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_migrations (
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_version TEXT NOT NULL,
        checksum TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}',
        migrated_at TEXT NOT NULL,
        PRIMARY KEY (source, source_id)
    )
    """,
)


def sqlite_path_from_url(database_url: str) -> str:
    """将 sqlite:/// URL 转换为 sqlite3 可接受的路径。"""

    prefix = "sqlite:///"
    if not database_url.lower().startswith(prefix):
        raise ValueError(
            "SQLite URL 必须以 sqlite:/// 开头"
        )

    raw_path = unquote(database_url[len(prefix):])
    if not raw_path:
        raise ValueError("SQLite URL 缺少数据库路径")
    if "?" in raw_path or "#" in raw_path:
        raise ValueError("SQLite URL 暂不支持 query 或 fragment")
    if raw_path == ":memory:":
        return raw_path

    if re.match(r"^[A-Za-z]:[/\\]", raw_path):
        return str(Path(raw_path))

    return str(Path(raw_path).expanduser().resolve())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime_to_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _datetime_from_text(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        session_id=row["session_id"],
        turn_no=int(row["turn_no"]),
        status=row["status"],
        base_event_id=int(row["base_event_id"]),
        checkpoint_thread_id=row["checkpoint_thread_id"],
        heartbeat_at=_datetime_from_text(row["heartbeat_at"]),
        started_at=cast(
            datetime,
            _datetime_from_text(row["started_at"]),
        ),
        finished_at=_datetime_from_text(row["finished_at"]),
        error=row["error"],
    )


def _event_record(row: sqlite3.Row) -> EventRecord:
    payload = json.loads(row["payload"])
    if not isinstance(payload, dict):
        raise ValueError(
            f"event_id={row['event_id']} 的 payload 不是 JSON object"
        )

    return EventRecord(
        event_id=int(row["event_id"]),
        session_id=row["session_id"],
        run_id=row["run_id"],
        turn_no=int(row["turn_no"]),
        ordinal=int(row["ordinal"]),
        message_id=row["message_id"],
        event_type=row["event_type"],
        payload=payload,
        created_at=cast(
            datetime,
            _datetime_from_text(row["created_at"]),
        ),
    )


class SQLiteConversationStore:
    """SQLite 对话日志实现，适合本地和单实例部署。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.database_path = sqlite_path_from_url(database_url)
        self._keeper: sqlite3.Connection | None = None

        if self.database_path == ":memory:":
            self._connection_target = (
                f"file:conversation-store-{uuid4().hex}"
                "?mode=memory&cache=shared"
            )
            self._connection_uses_uri = True
            self._keeper = self._open_connection()
        else:
            self._connection_target = self.database_path
            self._connection_uses_uri = False

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._connection_target,
            timeout=30,
            isolation_level=None,
            uri=self._connection_uses_uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _connect(self) -> sqlite3.Connection:
        return self._open_connection()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def close(self) -> None:
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def setup(self) -> None:
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for statement in SQLITE_SCHEMA_STATEMENTS:
                connection.execute(statement)
            # Existing installations created by an earlier draft may not
            # have defaults, so setup never depends on implicit timestamps.
            connection.execute(
                """
                UPDATE conversation_sessions
                SET created_at = COALESCE(created_at, ?),
                    updated_at = COALESCE(updated_at, ?)
                """,
                (now, now),
            )

    def ensure_session(self, session_id: str) -> None:
        cleaned_session_id = _clean_identifier(
            session_id,
            field_name="session_id",
        )
        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO conversation_sessions (
                    session_id, created_at, updated_at
                ) VALUES (?, ?, ?)
                """,
                (cleaned_session_id, now, now),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        cleaned_run_id = _clean_identifier(run_id, field_name="run_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_runs WHERE run_id = ?",
                (cleaned_run_id,),
            ).fetchone()
        return _run_record(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        cleaned_session_id = _clean_identifier(
            session_id,
            field_name="session_id",
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM conversation_sessions WHERE session_id = ?",
                (cleaned_session_id,),
            )
        return cursor.rowcount > 0

    def interrupt_running_runs(
        self,
        *,
        reason: str = "process restarted",
    ) -> int:
        cleaned_reason = reason.strip() or "process restarted"
        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE conversation_runs
                SET status = 'interrupted',
                    finished_at = ?,
                    error = ?
                WHERE status = 'running'
                """,
                (now, cleaned_reason),
            )
        return cursor.rowcount

    def begin_run(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> RunRecord:
        cleaned_session_id = _clean_identifier(
            session_id,
            field_name="session_id",
        )
        cleaned_run_id = _clean_identifier(
            run_id or f"run-{uuid4().hex}",
            field_name="run_id",
        )
        now = _datetime_to_text(_utc_now())

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO conversation_sessions (
                    session_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?)
                """,
                (cleaned_session_id, now, now),
            )
            active_row = connection.execute(
                """
                SELECT run_id
                FROM conversation_runs
                WHERE session_id = ? AND status = 'running'
                LIMIT 1
                """,
                (cleaned_session_id,),
            ).fetchone()
            if active_row is not None:
                raise ActiveRunError(
                    "当前会话已经存在正在运行的任务："
                    f"{active_row['run_id']}"
                )

            session_row = connection.execute(
                """
                SELECT next_turn_no
                FROM conversation_sessions
                WHERE session_id = ?
                """,
                (cleaned_session_id,),
            ).fetchone()
            if session_row is None:
                raise RuntimeError("创建或读取 conversation session 失败")

            turn_no = int(session_row["next_turn_no"])
            event_row = connection.execute(
                """
                SELECT COALESCE(MAX(event_id), 0) AS base_event_id
                FROM conversation_events
                WHERE session_id = ?
                """,
                (cleaned_session_id,),
            ).fetchone()
            base_event_id = int(event_row["base_event_id"])

            connection.execute(
                """
                INSERT INTO conversation_runs (
                    run_id,
                    session_id,
                    turn_no,
                    status,
                    base_event_id,
                    started_at
                ) VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (
                    cleaned_run_id,
                    cleaned_session_id,
                    turn_no,
                    base_event_id,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET next_turn_no = next_turn_no + 1,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (now, cleaned_session_id),
            )
            row = connection.execute(
                """
                SELECT * FROM conversation_runs WHERE run_id = ?
                """,
                (cleaned_run_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("创建 conversation run 失败")
        return _run_record(row)

    def append_event(
        self,
        run_id: str,
        *,
        message_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> EventRecord:
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

        try:
            payload_text = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"payload 不能编码为 JSON：{exc}"
            ) from exc

        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run_row = connection.execute(
                """
                SELECT run_id, session_id, turn_no, status
                FROM conversation_runs
                WHERE run_id = ?
                """,
                (cleaned_run_id,),
            ).fetchone()
            if run_row is None:
                raise RunNotFoundError(
                    f"运行不存在：{cleaned_run_id}"
                )

            existing_row = connection.execute(
                """
                SELECT *
                FROM conversation_events
                WHERE run_id = ? AND message_id = ?
                """,
                (cleaned_run_id, cleaned_message_id),
            ).fetchone()
            if existing_row is not None:
                existing = _event_record(existing_row)
                if (
                    existing.event_type != cleaned_event_type
                    or existing.payload != payload
                ):
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

            ordinal_row = connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                FROM conversation_events
                WHERE run_id = ?
                """,
                (cleaned_run_id,),
            ).fetchone()
            next_ordinal = int(ordinal_row["next_ordinal"])
            cursor = connection.execute(
                """
                INSERT INTO conversation_events (
                    session_id,
                    run_id,
                    turn_no,
                    ordinal,
                    message_id,
                    event_type,
                    payload,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_row["session_id"],
                    cleaned_run_id,
                    run_row["turn_no"],
                    next_ordinal,
                    cleaned_message_id,
                    cleaned_event_type,
                    payload_text,
                    now,
                ),
            )
            event_row = connection.execute(
                """
                SELECT * FROM conversation_events WHERE event_id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        if event_row is None:
            raise RuntimeError("创建 conversation event 失败")
        return _event_record(event_row)

    def append_message(
        self,
        run_id: str,
        message: BaseMessage,
        *,
        message_id: str | None = None,
    ) -> EventRecord:
        resolved_message_id = _message_identifier(
            message,
            message_id,
        )
        return self.append_event(
            run_id,
            message_id=resolved_message_id,
            event_type=cast(
                EventType,
                event_type_for_message(message),
            ),
            payload=serialize_message(message),
        )

    def list_events(
        self,
        session_id: str,
        *,
        statuses: Iterable[str] | None = None,
    ) -> list[EventRecord]:
        cleaned_session_id = _clean_identifier(
            session_id,
            field_name="session_id",
        )
        cleaned_statuses = (
            _clean_run_statuses(statuses)
            if statuses is not None
            else None
        )
        query = """
            SELECT event.*
            FROM conversation_events AS event
            JOIN conversation_runs AS run
              ON run.run_id = event.run_id
            WHERE event.session_id = ?
        """
        parameters: list[Any] = [cleaned_session_id]
        if cleaned_statuses is not None:
            placeholders = ", ".join(
                "?" for _ in cleaned_statuses
            )
            query += f" AND run.status IN ({placeholders})"
            parameters.extend(cleaned_statuses)
        query += " ORDER BY event.turn_no, event.ordinal"

        with self._connection() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()
        return [_event_record(row) for row in rows]

    def load_messages(
        self,
        session_id: str,
        *,
        statuses: Iterable[str] = ("completed",),
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for event in self.list_events(
            session_id,
            statuses=statuses,
        ):
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
        self,
        session_id: str,
    ) -> ContextRecord | None:
        cleaned_session_id = _clean_identifier(
            session_id,
            field_name="session_id",
        )
        with self._connection() as connection:
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
                WHERE session_id = ?
                """,
                (cleaned_session_id,),
            ).fetchone()

        if row is None:
            return None
        payloads = json.loads(row["projected_messages"])
        compression_session = decode_json_value(
            json.loads(row["compression_session"])
        )
        if not isinstance(payloads, list):
            raise ValueError(
                "conversation_context.projected_messages 必须是数组"
            )
        if not isinstance(compression_session, dict):
            raise ValueError(
                "conversation_context.compression_session 必须是对象"
            )
        return ContextRecord(
            session_id=row["session_id"],
            through_event_id=int(row["through_event_id"]),
            version=int(row["version"]),
            projected_messages=[
                deserialize_message(payload) for payload in payloads
            ],
            compression_session=compression_session,
            updated_at=cast(
                datetime,
                _datetime_from_text(row["updated_at"]),
            ),
        )

    def commit_run(
        self,
        run_id: str,
        *,
        projected_messages: Iterable[BaseMessage],
        compression_session: dict[str, Any],
    ) -> RunRecord:
        """原子提交 completed Run 和最新模型上下文投影。"""

        cleaned_run_id = _clean_identifier(
            run_id,
            field_name="run_id",
        )
        if not isinstance(compression_session, dict):
            raise TypeError("compression_session 必须是 dict")
        projected_text = json.dumps(
            [serialize_message(message) for message in projected_messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        compression_text = json.dumps(
            encode_json_value(compression_session),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        now = _datetime_to_text(_utc_now())

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM conversation_runs WHERE run_id = ?",
                (cleaned_run_id,),
            ).fetchone()
            if row is None:
                raise RunNotFoundError(
                    f"运行不存在：{cleaned_run_id}"
                )

            current = _run_record(row)
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
                WHERE session_id = ?
                """,
                (current.session_id,),
            ).fetchone()
            through_event_id = int(event_row["through_event_id"])

            connection.execute(
                """
                UPDATE conversation_runs
                SET status = 'completed', finished_at = ?, error = NULL
                WHERE run_id = ?
                """,
                (now, cleaned_run_id),
            )
            connection.execute(
                """
                INSERT INTO conversation_context (
                    session_id,
                    through_event_id,
                    version,
                    projected_messages,
                    compression_session,
                    updated_at
                ) VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    through_event_id = excluded.through_event_id,
                    version = conversation_context.version + 1,
                    projected_messages = excluded.projected_messages,
                    compression_session = excluded.compression_session,
                    updated_at = excluded.updated_at
                """,
                (
                    current.session_id,
                    through_event_id,
                    projected_text,
                    compression_text,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?
                WHERE session_id = ?
                """,
                (now, current.session_id),
            )
            finished_row = connection.execute(
                "SELECT * FROM conversation_runs WHERE run_id = ?",
                (cleaned_run_id,),
            ).fetchone()

        if finished_row is None:
            raise RuntimeError("提交 conversation run 失败")
        return _run_record(finished_row)

    def replace_context(
        self,
        session_id: str,
        *,
        projected_messages: Iterable[BaseMessage],
        compression_session: dict[str, Any],
    ) -> ContextRecord:
        cleaned_session_id = _clean_identifier(
            session_id,
            field_name="session_id",
        )
        if not isinstance(compression_session, dict):
            raise TypeError("compression_session 必须是 dict")
        projected_text = json.dumps(
            [serialize_message(message) for message in projected_messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        compression_text = json.dumps(
            encode_json_value(compression_session),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM conversation_sessions WHERE session_id = ?",
                (cleaned_session_id,),
            ).fetchone() is None:
                raise ValueError(f"会话不存在：{cleaned_session_id}")
            event_row = connection.execute(
                """
                SELECT COALESCE(MAX(event_id), 0) AS through_event_id
                FROM conversation_events WHERE session_id = ?
                """,
                (cleaned_session_id,),
            ).fetchone()
            through_event_id = int(event_row["through_event_id"])
            connection.execute(
                """
                INSERT INTO conversation_context (
                    session_id, through_event_id, version,
                    projected_messages, compression_session, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    through_event_id = excluded.through_event_id,
                    version = conversation_context.version + 1,
                    projected_messages = excluded.projected_messages,
                    compression_session = excluded.compression_session,
                    updated_at = excluded.updated_at
                """,
                (
                    cleaned_session_id,
                    through_event_id,
                    projected_text,
                    compression_text,
                    now,
                ),
            )
        context = self.load_context(cleaned_session_id)
        if context is None:
            raise RuntimeError("重建 conversation context 失败")
        return context

    def get_migration(
        self,
        source: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT source, source_id, source_version, checksum,
                       details, migrated_at
                FROM conversation_migrations
                WHERE source = ? AND source_id = ?
                """,
                (source, source_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["details"] = json.loads(result["details"])
        return result

    def record_migration(
        self,
        source: str,
        source_id: str,
        *,
        source_version: str,
        checksum: str,
        details: dict[str, Any],
    ) -> None:
        details_text = json.dumps(
            details,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT checksum FROM conversation_migrations
                WHERE source = ? AND source_id = ?
                """,
                (source, source_id),
            ).fetchone()
            if existing is not None and existing["checksum"] != checksum:
                raise EventConflictError(
                    f"迁移来源内容已改变：{source}/{source_id}"
                )
            connection.execute(
                """
                INSERT INTO conversation_migrations (
                    source, source_id, source_version, checksum,
                    details, migrated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    source_version = excluded.source_version,
                    details = excluded.details,
                    migrated_at = excluded.migrated_at
                """,
                (
                    source,
                    source_id,
                    source_version,
                    checksum,
                    details_text,
                    now,
                ),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: FinalRunStatus,
        error: str | None = None,
    ) -> RunRecord:
        cleaned_run_id = _clean_identifier(
            run_id,
            field_name="run_id",
        )
        cleaned_status = _clean_final_run_status(status)
        cleaned_error = error.strip() if error else None
        if cleaned_status == "failed" and not cleaned_error:
            raise ValueError("failed run 必须提供 error")

        now = _datetime_to_text(_utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM conversation_runs WHERE run_id = ?
                """,
                (cleaned_run_id,),
            ).fetchone()
            if row is None:
                raise RunNotFoundError(
                    f"运行不存在：{cleaned_run_id}"
                )

            current = _run_record(row)
            if current.status == cleaned_status:
                return current
            if current.status != "running":
                raise RunStateConflictError(
                    "已经结束的 run 不能转换到另一个状态；"
                    f"当前状态：{current.status}，"
                    f"目标状态：{cleaned_status}"
                )

            connection.execute(
                """
                UPDATE conversation_runs
                SET status = ?, finished_at = ?, error = ?
                WHERE run_id = ?
                """,
                (
                    cleaned_status,
                    now,
                    cleaned_error,
                    cleaned_run_id,
                ),
            )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?
                WHERE session_id = ?
                """,
                (now, current.session_id),
            )
            finished_row = connection.execute(
                """
                SELECT * FROM conversation_runs WHERE run_id = ?
                """,
                (cleaned_run_id,),
            ).fetchone()

        if finished_row is None:
            raise RuntimeError("结束 conversation run 失败")
        return _run_record(finished_row)

    def complete_run(self, run_id: str) -> RunRecord:
        return self.finish_run(run_id, status="completed")

    def cancel_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> RunRecord:
        return self.finish_run(
            run_id,
            status="cancelled",
            error=reason,
        )

    def fail_run(
        self,
        run_id: str,
        *,
        error: str,
    ) -> RunRecord:
        return self.finish_run(
            run_id,
            status="failed",
            error=error,
        )

    def interrupt_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> RunRecord:
        return self.finish_run(
            run_id,
            status="interrupted",
            error=reason,
        )
