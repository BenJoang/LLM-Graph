from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.persistence.conversation_store import create_conversation_store
from src.services import tool_agent_runner as runner_module
from src.services.tool_agent_runner import (
    TOOL_AGENT_ENTRYPOINT,
    ToolAgentRunner,
    stream_managed_graph,
)


def _run_arguments(session_id: str) -> dict:
    return {
        "question": "查询天气",
        "session_id": session_id,
        "profile_name": "test-main",
        "vision_profile_name": "test-vision",
        "recursion_limit": 20,
        "working_dir": None,
        "context_window_tokens": 4096,
    }


@pytest.mark.asyncio
async def test_graph_adapter_builds_without_checkpointer(
    monkeypatch,
):
    captured = {}

    class FakeGraph:
        async def astream(self, initial_state, **kwargs):
            captured["initial_state"] = initial_state
            captured.update(kwargs)
            yield {"assistant": {"messages": [AIMessage(content="ok")]}}

    def fake_build_graph(**kwargs):
        captured["build"] = kwargs
        return FakeGraph()

    monkeypatch.setattr(
        runner_module,
        "load_graph_builder",
        lambda _entrypoint: fake_build_graph,
    )

    updates = [update async for update in stream_managed_graph(
        graph_entrypoint=TOOL_AGENT_ENTRYPOINT,
        initial_state={"messages": [], "turn_id": 3},
        profile_name="main",
        vision_profile_name="vision",
        recursion_limit=25,
        working_dir=None,
        context_window_tokens=8192,
    )]

    assert len(updates) == 1
    assert captured["build"]["checkpointer"] is None
    assert captured["config"] == {"recursion_limit": 25}
    assert captured["stream_mode"] == "updates"
    assert "durability" not in captured


@pytest.mark.asyncio
async def test_runner_persists_updates_and_replays_completed_history(
    tmp_path: Path,
):
    database_url = (
        "sqlite:///"
        + (tmp_path / "runner.sqlite3").as_posix()
    )
    store = create_conversation_store(database_url)
    calls = []

    async def fake_graph_stream(**kwargs):
        calls.append(kwargs)
        yield {
            "assistant": {
                "messages": [AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "weather",
                        "args": {"city": "上海"},
                        "id": "call-1",
                        "type": "tool_call",
                    }],
                )],
            }
        }
        yield {
            "tools": {
                "messages": [ToolMessage(
                    content="晴，28°C",
                    tool_call_id="call-1",
                    name="weather",
                )],
            }
        }
        yield {
            "assistant": {
                "messages": [AIMessage(content="上海天气晴朗")],
            }
        }

    runner = ToolAgentRunner(
        store=store,
        graph_stream=fake_graph_stream,
    )
    try:
        first = await runner.run(**_run_arguments("runner-session"))

        assert [message.type for message in first["messages"]] == [
            "human",
            "ai",
            "tool",
            "ai",
        ]
        assert len(store.load_messages("runner-session")) == 4
        assert len(store.list_events(
            "runner-session",
            statuses=("completed",),
        )) == 4
        assert "checkpoint_thread_id" not in calls[0]
        context = store.load_context("runner-session")
        assert context is not None
        assert len(context.projected_messages) == 4

        second_arguments = _run_arguments("runner-session")
        second_arguments["question"] = "那明天呢？"
        second = await runner.run(**second_arguments)

        second_initial = calls[1]["initial_state"]
        assert len(second_initial["messages"]) == 5
        assert second_initial["turn_id"] == 2
        assert len(second["messages"]) == 8
        assert len(store.load_messages("runner-session")) == 8
        assert store.load_context("runner-session").version == 2
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_runner_marks_failed_and_excludes_partial_messages(
    tmp_path: Path,
):
    database_url = (
        "sqlite:///"
        + (tmp_path / "failed.sqlite3").as_posix()
    )
    store = create_conversation_store(database_url)

    async def failing_graph_stream(**_kwargs):
        yield {
            "assistant": {
                "messages": [AIMessage(content="处理中")],
            }
        }
        raise RuntimeError("provider unavailable")

    runner = ToolAgentRunner(
        store=store,
        graph_stream=failing_graph_stream,
    )
    try:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await runner.run(**_run_arguments("failed-session"))

        assert len(store.list_events(
            "failed-session",
            statuses=("failed",),
        )) == 2
        assert store.load_messages("failed-session") == []
        assert store.load_context("failed-session") is None
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_update_callback_runs_after_message_is_in_sql(tmp_path: Path):
    store = create_conversation_store(
        "sqlite:///" + (tmp_path / "callback.sqlite").as_posix()
    )

    async def fake_graph_stream(**_kwargs):
        yield {"assistant": {"messages": [AIMessage(content="完成")]}}

    seen_counts = []
    runner = ToolAgentRunner(store=store, graph_stream=fake_graph_stream)

    async def on_update(_update):
        seen_counts.append(len(store.list_events("callback-session", statuses=None)))

    try:
        await runner.execute(
            **_run_arguments("callback-session"),
            on_update=on_update,
        )
        assert seen_counts == [2]
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_timeout_marks_run_failed(tmp_path: Path):
    store = create_conversation_store(
        "sqlite:///" + (tmp_path / "timeout.sqlite").as_posix()
    )

    async def slow_graph_stream(**_kwargs):
        await asyncio.Event().wait()
        yield {}

    runner = ToolAgentRunner(store=store, graph_stream=slow_graph_stream)
    try:
        with pytest.raises(TimeoutError):
            await runner.execute(
                **_run_arguments("timeout-session"),
                timeout_seconds=0.01,
            )
        record = store.get_run(
            _find_only_run_id(tmp_path / "timeout.sqlite")
        )
        assert record is not None and record.status == "failed"
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_runner_marks_cancelled_and_excludes_partial_context(
    tmp_path: Path,
):
    database_url = (
        "sqlite:///"
        + (tmp_path / "cancelled.sqlite3").as_posix()
    )
    store = create_conversation_store(database_url)
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def blocking_graph_stream(**_kwargs):
        entered.set()
        await blocker.wait()
        yield {"assistant": {"messages": [AIMessage(content="完成")]}}

    runner = ToolAgentRunner(
        store=store,
        graph_stream=blocking_graph_stream,
    )
    try:
        task = asyncio.create_task(
            runner.run(**_run_arguments("cancelled-session"))
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(store.list_events(
            "cancelled-session",
            statuses=("cancelled",),
        )) == 1
        assert store.load_messages("cancelled-session") == []
        assert store.load_context("cancelled-session") is None
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_cancellation_during_begin_does_not_leave_running_run(
    tmp_path: Path,
):
    database_path = tmp_path / "cancel-during-begin.sqlite3"
    store = create_conversation_store(
        "sqlite:///" + database_path.as_posix()
    )
    begin_entered = threading.Event()
    release_begin = threading.Event()

    class BlockingBeginStore:
        database_url = store.database_url

        def __getattr__(self, name):
            return getattr(store, name)

        def begin_run(self, *args, **kwargs):
            begin_entered.set()
            release_begin.wait(timeout=2)
            return store.begin_run(*args, **kwargs)

    async def unused_graph_stream(**_kwargs):
        raise AssertionError("cancelled begin must not start graph")
        yield {}

    runner = ToolAgentRunner(
        store=BlockingBeginStore(),
        graph_stream=unused_graph_stream,
    )
    try:
        task = asyncio.create_task(
            runner.run(**_run_arguments("begin-cancel-session"))
        )
        entered = await asyncio.to_thread(begin_entered.wait, 2)
        assert entered
        task.cancel()
        release_begin.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        runs = store.list_events(
            "begin-cancel-session",
            statuses=("cancelled",),
        )
        assert runs == []
        record = store.get_run(_find_only_run_id(database_path))
        assert record is not None and record.status == "cancelled"
    finally:
        release_begin.set()
        await runner.close()


@pytest.mark.asyncio
async def test_completion_commit_wins_over_late_cancellation(tmp_path: Path):
    store = create_conversation_store(
        "sqlite:///" + (tmp_path / "commit-boundary.sqlite").as_posix()
    )
    commit_entered = threading.Event()
    release_commit = threading.Event()

    class BlockingCommitStore:
        database_url = store.database_url

        def __getattr__(self, name):
            return getattr(store, name)

        def commit_run(self, *args, **kwargs):
            commit_entered.set()
            release_commit.wait(timeout=2)
            return store.commit_run(*args, **kwargs)

    async def graph_stream(**_kwargs):
        yield {"assistant": {"messages": [AIMessage(content="完成")]}}

    runner = ToolAgentRunner(
        store=BlockingCommitStore(),
        graph_stream=graph_stream,
    )
    task = asyncio.create_task(
        runner.run(**_run_arguments("commit-boundary-session"))
    )
    try:
        assert await asyncio.to_thread(commit_entered.wait, 2)
        task.cancel()
        release_commit.set()
        result = await task
        assert result["messages"][-1].content == "完成"
        record = store.get_run(
            _find_only_run_id(tmp_path / "commit-boundary.sqlite")
        )
        assert record is not None and record.status == "completed"
    finally:
        release_commit.set()
        await runner.close()


def _find_only_run_id(database_path: Path) -> str:
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT run_id FROM conversation_runs"
        ).fetchone()
    assert row is not None
    return str(row[0])
