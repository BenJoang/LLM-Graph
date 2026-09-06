from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from scripts.migrate_checkpoint_conversations import (
    LegacySession,
    _checksum,
    _group_turns,
    migrate_session,
)
from src.api.gui_store import SessionRecord
from src.context.message_context import set_context_state
from src.persistence.conversation_store import create_conversation_store


def _human(content: str, message_id: str, turn_no: int) -> HumanMessage:
    message = HumanMessage(content=content, id=message_id)
    set_context_state(message, turn_id=turn_no)
    return message


def test_migration_is_idempotent_and_excludes_interrupted_turn(tmp_path: Path):
    messages = [
        _human("第一轮", "h1", 1),
        AIMessage(content="完成", id="a1"),
        _human("第二轮", "h2", 2),
        AIMessage(
            content="",
            id="a2",
            tool_calls=[{
                "name": "test",
                "args": {},
                "id": "missing-result",
                "type": "tool_call",
            }],
        ),
    ]
    compression = {
        "version": 1,
        "collapse_commits": [],
        "collapse_message_ids": [],
    }
    metadata = SessionRecord(
        id="gui-migration-test",
        title="test",
        profile_name="main",
        vision_profile_name="vision",
        working_dir=str(tmp_path),
        context_window_tokens=4096,
        recursion_limit=20,
        graph_entrypoint="src.graphs.tool_agent_graph:astream_tool_agent",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        archived=False,
    )
    source = LegacySession(
        metadata=metadata,
        messages=messages,
        turns=_group_turns(messages),
        compression_session=compression,
        checkpoint_id="checkpoint-test",
        checksum=_checksum(messages, compression),
    )
    store = create_conversation_store(
        "sqlite:///" + (tmp_path / "target.sqlite").as_posix()
    )
    store.setup()
    try:
        migrate_session(store, source)
        migrate_session(store, source)
        assert len(store.list_events(metadata.id, statuses=None)) == 4
        assert len(store.load_messages(metadata.id)) == 2
        assert store.get_run(
            "migration:gui-migration-test:1"
        ).status == "completed"
        assert store.get_run(
            "migration:gui-migration-test:2"
        ).status == "interrupted"
        assert len(store.load_context(metadata.id).projected_messages) == 2
    finally:
        store.close()
