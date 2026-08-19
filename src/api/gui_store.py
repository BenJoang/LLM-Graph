from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "outputs" / "gui_state.sqlite"
CONFIG_PATH = PROJECT_ROOT / "config" / "user_config.json"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SessionRecord:
    id: str
    title: str
    profile_name: str
    vision_profile_name: str
    working_dir: str
    context_window_tokens: int
    recursion_limit: int
    created_at: str
    updated_at: str
    archived: bool

    def to_dict(self) -> dict:
        return asdict(self)


class GuiStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        configured = os.environ.get("LLM_GRAPH_GUI_DB")
        self.db_path = Path(db_path or configured or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._setup()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gui_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    profile_name TEXT NOT NULL,
                    vision_profile_name TEXT NOT NULL,
                    working_dir TEXT NOT NULL,
                    context_window_tokens INTEGER NOT NULL,
                    recursion_limit INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_gui_sessions_updated "
                "ON gui_sessions(archived, updated_at DESC)"
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> SessionRecord:
        values = dict(row)
        values["archived"] = bool(values["archived"])
        return SessionRecord(**values)

    def create_session(
        self,
        *,
        profile_name: str,
        vision_profile_name: str,
        working_dir: str,
        context_window_tokens: int,
        recursion_limit: int,
        title: str = "新会话",
    ) -> SessionRecord:
        now = utc_now()
        record = SessionRecord(
            id=f"gui-{uuid4().hex}",
            title=title.strip() or "新会话",
            profile_name=profile_name,
            vision_profile_name=vision_profile_name,
            working_dir=working_dir,
            context_window_tokens=context_window_tokens,
            recursion_limit=recursion_limit,
            created_at=now,
            updated_at=now,
            archived=False,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gui_sessions (
                    id, title, profile_name, vision_profile_name, working_dir,
                    context_window_tokens, recursion_limit, created_at,
                    updated_at, archived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.title,
                    record.profile_name,
                    record.vision_profile_name,
                    record.working_dir,
                    record.context_window_tokens,
                    record.recursion_limit,
                    record.created_at,
                    record.updated_at,
                    int(record.archived),
                ),
            )
        return record

    def list_sessions(self, archived: bool = False) -> list[SessionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM gui_sessions WHERE archived = ? "
                "ORDER BY updated_at DESC",
                (int(archived),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM gui_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._record(row) if row else None

    def update_session(self, session_id: str, patch: dict) -> SessionRecord | None:
        allowed = {
            "title",
            "profile_name",
            "vision_profile_name",
            "working_dir",
            "context_window_tokens",
            "recursion_limit",
            "archived",
        }
        values = {key: value for key, value in patch.items() if key in allowed}
        if not values:
            return self.get_session(session_id)
        if "archived" in values:
            values["archived"] = int(bool(values["archived"]))
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        parameters = [*values.values(), session_id]
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE gui_sessions SET {assignments} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                return None
        return self.get_session(session_id)

    def touch_with_question(self, session_id: str, question: str) -> SessionRecord | None:
        record = self.get_session(session_id)
        if record is None:
            return None
        patch: dict = {}
        if record.title == "新会话":
            patch["title"] = question.strip().replace("\n", " ")[:40] or "新会话"
        patch["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in patch)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE gui_sessions SET {assignments} WHERE id = ?",
                [*patch.values(), session_id],
            )
        return self.get_session(session_id)


def load_safe_profiles(config_path: str | Path = CONFIG_PATH) -> list[dict]:
    with Path(config_path).open("r", encoding="utf-8") as file:
        profiles = json.load(file).get("profiles", {})
    return [
        {
            "name": name,
            "model": profile.get("model", name),
            "support_tools": bool(profile.get("support_tools", False)),
        }
        for name, profile in profiles.items()
    ]
