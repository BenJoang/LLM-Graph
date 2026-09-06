from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.persistence.conversation_store import (
    ActiveRunError,
    ConversationStore,
    EventConflictError,
    RunNotWritableError,
    RunStateConflictError,
    conversation_database_url,
    create_conversation_store,
)
from src.persistence.message_codec import (
    MessageCodecError,
    UnsupportedMessageTypeError,
    deserialize_message,
    serialize_message,
)


def _sample_messages():
    return [
        SystemMessage(
            content="You are concise.",
            id="system-1",
            name="policy",
            additional_kwargs={"source": "test"},
        ),
        HumanMessage(
            content=[
                {"type": "text", "text": "查询天气"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/a.png"},
                },
            ],
            id="human-1",
            response_metadata={"trace": "human-trace"},
        ),
        AIMessage(
            content="",
            id="assistant-1",
            tool_calls=[
                {
                    "name": "weather",
                    "args": {"city": "上海"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        ),
        ToolMessage(
            content="晴，28°C",
            id="tool-1",
            name="weather",
            tool_call_id="call-1",
            artifact={"provider": "test", "raw": [28, "sunny"]},
            status="success",
        ),
    ]


@pytest.fixture(params=("sqlite", "postgres"))
def conversation_store(request, tmp_path: Path):
    backend = request.param
    if backend == "sqlite":
        database_path = tmp_path / "conversation.sqlite3"
        database_url = f"sqlite:///{database_path.as_posix()}"
    else:
        if os.getenv("LLM_GRAPH_TEST_POSTGRES") != "1":
            pytest.skip(
                "设置 LLM_GRAPH_TEST_POSTGRES=1 后运行 PostgreSQL 契约测试"
            )
        database_url = conversation_database_url()

    store = create_conversation_store(database_url)
    store.setup()
    session_id = f"conversation-store-test-{uuid4().hex}"

    yield backend, store, session_id

    if backend == "postgres":
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(
                """
                DELETE FROM conversation_sessions
                WHERE session_id = %s
                """,
                (session_id,),
            )


def test_message_codec_round_trip_preserves_full_messages():
    for original in _sample_messages():
        payload = serialize_message(original)
        restored = deserialize_message(payload)

        assert serialize_message(restored) == payload
        assert restored.id == original.id


def test_message_codec_rejects_partial_and_unknown_payloads():
    with pytest.raises(UnsupportedMessageTypeError):
        serialize_message(AIMessageChunk(content="partial"))

    with pytest.raises(MessageCodecError):
        deserialize_message({
            "schema_version": 999,
            "type": "human",
            "data": {},
        })


def test_message_codec_preserves_postgres_incompatible_nul_text():
    original = ToolMessage(
        content="stdout:\n\x00binary-like text",
        id="nul-tool",
        tool_call_id="nul-call",
    )
    payload = serialize_message(original)
    assert "\\u0000" not in str(payload)
    restored = deserialize_message(payload)
    assert restored.content == original.content


def test_sqlite_memory_database_survives_separate_operations():
    store = create_conversation_store("sqlite:///:memory:")
    try:
        store.setup()
        run = store.begin_run("memory-session")
        store.append_message(
            run.run_id,
            HumanMessage(content="memory", id="memory-message"),
        )
        store.complete_run(run.run_id)

        assert len(store.load_messages("memory-session")) == 1
    finally:
        store.close()


def test_store_contract(conversation_store):
    _, store, session_id = conversation_store
    run = store.begin_run(session_id, run_id=f"run-{uuid4().hex}")

    with pytest.raises(ActiveRunError):
        store.begin_run(session_id)

    messages = _sample_messages()
    events = [store.append_message(run.run_id, message) for message in messages]
    assert [event.ordinal for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]

    repeated = store.append_message(run.run_id, messages[0])
    assert repeated.event_id == events[0].event_id

    with pytest.raises(EventConflictError):
        store.append_message(
            run.run_id,
            SystemMessage(content="changed", id="system-1"),
        )

    completed = store.complete_run(run.run_id)
    assert completed.status == "completed"
    assert completed.finished_at is not None
    assert store.complete_run(run.run_id).finished_at == completed.finished_at

    with pytest.raises(RunStateConflictError):
        store.fail_run(run.run_id, error="must not replace completion")

    with pytest.raises(RunNotWritableError):
        store.append_message(
            run.run_id,
            HumanMessage(content="late", id="late-message"),
        )

    restored = store.load_messages(session_id)
    assert [serialize_message(item) for item in restored] == [
        serialize_message(item) for item in messages
    ]

    cancelled_run = store.begin_run(
        session_id,
        run_id=f"run-{uuid4().hex}",
    )
    assert cancelled_run.turn_no == 2
    store.append_message(
        cancelled_run.run_id,
        HumanMessage(content="discarded", id="cancelled-message"),
    )
    store.cancel_run(cancelled_run.run_id, reason="user stopped")

    assert len(store.list_events(session_id)) == 5
    assert len(store.list_events(
        session_id,
        statuses=("cancelled",),
    )) == 1
    assert len(store.load_messages(session_id)) == 4


def test_terminal_state_race_has_one_winner(conversation_store):
    _, store, session_id = conversation_store
    run = store.begin_run(session_id, run_id=f"run-{uuid4().hex}")

    def complete():
        return store.complete_run(run.run_id)

    def cancel():
        return store.cancel_run(run.run_id, reason="race")

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(complete), executor.submit(cancel)]
        for future in futures:
            try:
                outcomes.append(("ok", future.result().status))
            except RunStateConflictError:
                outcomes.append(("conflict", None))

    assert sum(kind == "ok" for kind, _ in outcomes) == 1
    assert sum(kind == "conflict" for kind, _ in outcomes) == 1


def test_context_commit_is_atomic_and_round_trips(conversation_store):
    _, store, session_id = conversation_store
    run = store.begin_run(session_id)
    messages = _sample_messages()
    for message in messages:
        store.append_message(run.run_id, message)

    completed = store.commit_run(
        run.run_id,
        projected_messages=messages,
        compression_session={
            "version": 1,
            "collapse_commits": [],
            "collapse_message_ids": [],
        },
    )
    context = store.load_context(session_id)
    assert completed.status == "completed"
    assert context is not None
    assert context.through_event_id > 0
    assert [serialize_message(message) for message in context.projected_messages] == [
        serialize_message(message) for message in messages
    ]


def test_orphan_recovery_and_idempotent_delete(conversation_store):
    _, store, session_id = conversation_store
    run = store.begin_run(session_id)
    assert store.interrupt_running_runs(reason="test restart") >= 1
    recovered = store.get_run(run.run_id)
    assert recovered is not None
    assert recovered.status == "interrupted"
    assert recovered.error == "test restart"
    assert store.delete_session(session_id) is True
    assert store.delete_session(session_id) is False
