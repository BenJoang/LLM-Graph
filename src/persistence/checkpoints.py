# src/persistence/checkpoints.py

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_SQLITE_PATH = (
    PROJECT_ROOT / "outputs" / "checkpoints" / "tool_agent.sqlite"
)


def checkpoint_backend() -> str:
    """Return the configured checkpoint backend.

    SQLite remains the default so a fresh checkout works without PostgreSQL.
    """

    backend = os.getenv(
        "LLM_GRAPH_CHECKPOINT_BACKEND",
        "sqlite",
    ).strip().lower()

    if backend not in {"sqlite", "postgres"}:
        raise RuntimeError(
            "LLM_GRAPH_CHECKPOINT_BACKEND 必须是 sqlite 或 postgres"
        )

    return backend


def sqlite_path() -> Path:
    """Resolve the SQLite checkpoint path relative to the project root."""

    configured = os.getenv("LLM_GRAPH_CHECKPOINT_SQLITE_PATH")
    path = Path(configured) if configured else DEFAULT_SQLITE_PATH

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    return path.resolve()


def postgres_url() -> str:
    """Return the PostgreSQL URL without exposing it to GUI clients."""

    value = os.getenv("LLM_GRAPH_CHECKPOINT_POSTGRES_URL", "").strip()

    if not value:
        raise RuntimeError(
            "选择 postgres 时必须配置 "
            "LLM_GRAPH_CHECKPOINT_POSTGRES_URL"
        )

    return value


@contextmanager
def open_checkpointer():
    """Open a synchronous saver for state reads and synchronous graph runs."""

    backend = checkpoint_backend()

    if backend == "sqlite":
        path = sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        with SqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
        return

    # Only load backend-specific modules when that backend is selected.
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(postgres_url()) as saver:
        yield saver


def delete_checkpoint_thread(thread_id: str) -> None:
    """Permanently delete every checkpoint row for one thread."""

    with open_checkpointer() as saver:
        saver.delete_thread(thread_id)


@asynccontextmanager
async def open_async_checkpointer():
    """Open an asynchronous saver for asynchronous graph runs."""

    backend = checkpoint_backend()

    if backend == "sqlite":
        path = sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(postgres_url()) as saver:
        yield saver


async def setup_checkpoint_backend() -> None:
    """Run PostgreSQL checkpoint migrations when that backend is selected."""

    if checkpoint_backend() != "postgres":
        return

    from langgraph.checkpoint.postgres import PostgresSaver

    connection_url = postgres_url()

    def setup() -> None:
        with PostgresSaver.from_conn_string(connection_url) as saver:
            saver.setup()

    # Psycopg async connections cannot run on Windows' ProactorEventLoop.
    # Running the one-time migrations synchronously in a worker thread keeps
    # FastAPI startup non-blocking and works on every supported event loop.
    await asyncio.to_thread(setup)
