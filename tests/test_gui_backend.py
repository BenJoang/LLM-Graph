import asyncio
import json
import os
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from src.api.gui_messages import message_to_dto, read_thread_messages
from src.api.graph_entrypoints import (
    DEFAULT_GRAPH_ENTRYPOINT,
    GraphEntrypointError,
    validate_graph_entrypoint,
)
from src.api.gui_runtime import RunConflictError, RunManager
from src.api.gui_store import GuiStore, load_safe_profiles
from src.persistence.checkpoints import (
    checkpoint_backend,
    open_checkpointer,
    delete_checkpoint_thread,
    postgres_url,
    setup_checkpoint_backend,
    sqlite_path,
)


class GuiStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = GuiStore(Path(self.temp_dir.name) / "gui.sqlite")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_crud_and_automatic_title(self):
        session = self.store.create_session(
            profile_name="deepseekv4-flash",
            vision_profile_name="qwen3.8",
            working_dir=self.temp_dir.name,
            context_window_tokens=32768,
            recursion_limit=200,
        )
        self.assertEqual(session.title, "新会话")
        self.assertEqual(session.graph_entrypoint, DEFAULT_GRAPH_ENTRYPOINT)

        titled = self.store.touch_with_question(
            session.id,
            "请读取 README 并总结\n第二行",
        )
        self.assertEqual(titled.title, "请读取 README 并总结 第二行")

        configured = self.store.update_session(
            session.id,
            {
                "title": "手动标题",
                "context_window_tokens": 131072,
                "recursion_limit": 320,
            },
        )
        self.assertEqual(configured.title, "手动标题")
        self.assertEqual(configured.context_window_tokens, 131072)
        self.assertEqual(configured.recursion_limit, 320)

        archived = self.store.update_session(session.id, {"archived": True})
        self.assertTrue(archived.archived)
        self.assertEqual(self.store.list_sessions(), [])
        self.assertEqual(self.store.list_sessions(archived=True)[0].id, session.id)

        self.assertTrue(self.store.delete_session(session.id))
        self.assertIsNone(self.store.get_session(session.id))

    def test_existing_database_is_migrated_with_default_graph(self):
        database = Path(self.temp_dir.name) / "legacy.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE gui_sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    profile_name TEXT NOT NULL,
                    vision_profile_name TEXT NOT NULL,
                    working_dir TEXT NOT NULL,
                    context_window_tokens INTEGER NOT NULL,
                    recursion_limit INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "INSERT INTO gui_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy", "旧会话", "main", "vision", self.temp_dir.name,
                    32768, 100, "2026-01-01", "2026-01-01", 0,
                ),
            )
        connection.close()

        migrated = GuiStore(database).get_session("legacy")
        self.assertEqual(migrated.graph_entrypoint, DEFAULT_GRAPH_ENTRYPOINT)

    def test_graph_entrypoint_validation(self):
        self.assertEqual(
            validate_graph_entrypoint(DEFAULT_GRAPH_ENTRYPOINT),
            DEFAULT_GRAPH_ENTRYPOINT,
        )
        with self.assertRaises(GraphEntrypointError):
            validate_graph_entrypoint("os:path")
        with self.assertRaisesRegex(GraphEntrypointError, "异步生成器"):
            validate_graph_entrypoint("src.graphs.tool_agent_graph:build_graph")

    def test_profiles_are_safe(self):
        profiles = load_safe_profiles()
        self.assertGreater(len(profiles), 0)
        serialized = json.dumps(profiles)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("base_url", serialized)


class MessageTests(unittest.TestCase):
    def test_message_dto_keeps_tool_protocol_fields(self):
        assistant = message_to_dto(
            AIMessage(
                content="",
                tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "c1"}],
            )
        )
        tool = message_to_dto(
            ToolMessage(content="one match", tool_call_id="c1", name="grep")
        )
        self.assertEqual(assistant["tool_calls"][0]["id"], "c1")
        self.assertEqual(tool["tool_call_id"], "c1")
        self.assertEqual(tool["role"], "tool")


class CheckpointSettingsTests(unittest.TestCase):
    def test_sqlite_is_the_default_backend(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(checkpoint_backend(), "sqlite")
            asyncio.run(setup_checkpoint_backend())

    def test_relative_sqlite_path_is_resolved_from_project_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            relative = Path(Path(temp_dir).name) / "checkpoint.sqlite"
            with patch.dict(
                os.environ,
                {"LLM_GRAPH_CHECKPOINT_SQLITE_PATH": str(relative)},
                clear=True,
            ):
                resolved = sqlite_path()

        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "checkpoint.sqlite")

    def test_postgres_backend_requires_a_url(self):
        with patch.dict(
            os.environ,
            {"LLM_GRAPH_CHECKPOINT_BACKEND": "postgres"},
            clear=True,
        ):
            self.assertEqual(checkpoint_backend(), "postgres")
            with self.assertRaisesRegex(
                RuntimeError,
                "LLM_GRAPH_CHECKPOINT_POSTGRES_URL",
            ):
                postgres_url()

    def test_unknown_backend_is_rejected(self):
        with patch.dict(
            os.environ,
            {"LLM_GRAPH_CHECKPOINT_BACKEND": "mysql"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "sqlite 或 postgres"):
                checkpoint_backend()

    def test_gui_reads_messages_from_configured_sqlite_backend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_file = Path(temp_dir) / "checkpoints.sqlite"
            environment = {
                "LLM_GRAPH_CHECKPOINT_BACKEND": "sqlite",
                "LLM_GRAPH_CHECKPOINT_SQLITE_PATH": str(checkpoint_file),
            }

            builder = StateGraph(MessagesState)
            builder.add_node(
                "assistant",
                lambda _state: {"messages": [AIMessage(content="你好")]},
            )
            builder.add_edge(START, "assistant")
            builder.add_edge("assistant", END)

            with patch.dict(os.environ, environment, clear=True):
                with open_checkpointer() as saver:
                    graph = builder.compile(checkpointer=saver)
                    graph.invoke(
                        {"messages": [HumanMessage(content="测试")]},
                        {"configurable": {"thread_id": "gui-test"}},
                    )

                messages = read_thread_messages("gui-test")

        self.assertEqual(
            [message["content"] for message in messages],
            ["测试", "你好"],
        )

    def test_delete_checkpoint_thread_removes_sqlite_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_file = Path(temp_dir) / "checkpoints.sqlite"
            environment = {
                "LLM_GRAPH_CHECKPOINT_BACKEND": "sqlite",
                "LLM_GRAPH_CHECKPOINT_SQLITE_PATH": str(checkpoint_file),
            }
            builder = StateGraph(MessagesState)
            builder.add_node(
                "assistant",
                lambda _state: {"messages": [AIMessage(content="完成")]},
            )
            builder.add_edge(START, "assistant")
            builder.add_edge("assistant", END)
            with patch.dict(os.environ, environment, clear=True):
                with open_checkpointer() as saver:
                    graph = builder.compile(checkpointer=saver)
                    graph.invoke(
                        {"messages": [HumanMessage(content="删除我")]},
                        {"configurable": {"thread_id": "delete-test"}},
                    )
                self.assertTrue(read_thread_messages("delete-test"))
                delete_checkpoint_thread("delete-test")
                self.assertEqual(read_thread_messages("delete-test"), [])


class RunManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = GuiStore(Path(self.temp_dir.name) / "gui.sqlite")
        self.session = self.store.create_session(
            profile_name="deepseekv4-flash",
            vision_profile_name="qwen3.8",
            working_dir=self.temp_dir.name,
            context_window_tokens=32768,
            recursion_limit=200,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_run_emits_ordered_tool_events(self):
        async def fake_stream(**_kwargs):
            yield {
                "assistant": {
                    "messages": [AIMessage(
                        content="我来搜索",
                        tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "c1"}],
                    )]
                }
            }
            yield {
                "tools": {
                    "messages": [ToolMessage(content="one match", tool_call_id="c1", name="grep")]
                }
            }
            yield {"assistant": {"messages": [AIMessage(content="完成")]}}

        manager = RunManager(self.store, stream_agent=fake_stream)
        handle = await manager.start(self.session.id, "搜索 x")
        events = []
        while True:
            item = await asyncio.wait_for(handle.events.get(), timeout=2)
            if item is None:
                break
            events.append(item["event"])

        self.assertEqual(
            events,
            [
                "run.started",
                "assistant.step",
                "tool.started",
                "tool.finished",
                "assistant.step",
                "run.completed",
            ],
        )

    async def test_different_sessions_run_concurrently_and_same_session_conflicts(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        started: set[str] = set()

        async def blocking_stream(**kwargs):
            started.add(kwargs["thread_id"])
            if len(started) == 2:
                entered.set()
            await release.wait()
            yield {"assistant": {"messages": [AIMessage(content="完成")]}}

        manager = RunManager(self.store, stream_agent=blocking_stream)
        second = self.store.create_session(
            profile_name="deepseekv4-flash",
            vision_profile_name="qwen3.8",
            working_dir=self.temp_dir.name,
            context_window_tokens=32768,
            recursion_limit=200,
        )
        handle = await manager.start(self.session.id, "等待")
        second_handle = await manager.start(second.id, "同时等待")
        await asyncio.wait_for(entered.wait(), timeout=1)
        with self.assertRaises(RunConflictError):
            await manager.start(self.session.id, "第二个任务")
        await manager.cancel(handle.run_id)
        await asyncio.wait_for(handle.finished_event.wait(), timeout=2)
        self.assertFalse(second_handle.finished_event.is_set())
        release.set()
        await asyncio.wait_for(second_handle.finished_event.wait(), timeout=2)

        emitted = []
        while not handle.events.empty():
            item = handle.events.get_nowait()
            if item:
                emitted.append(item["event"])
        self.assertIn("run.cancelled", emitted)

    async def test_run_uses_updated_session_limits(self):
        configured = self.store.update_session(
            self.session.id,
            {"context_window_tokens": 131072, "recursion_limit": 350},
        )
        captured = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield {"assistant": {"messages": [AIMessage(content="完成")]}}

        manager = RunManager(self.store, stream_agent=fake_stream)
        handle = await manager.start(configured.id, "继续")
        await asyncio.wait_for(handle.finished_event.wait(), timeout=2)

        self.assertEqual(captured["context_window_tokens"], 131072)
        self.assertEqual(captured["recursion_limit"], 350)

    async def test_running_session_blocks_lifecycle_mutation(self):
        entered = asyncio.Event()

        async def blocking_stream(**_kwargs):
            entered.set()
            await asyncio.Event().wait()
            yield {}

        manager = RunManager(self.store, stream_agent=blocking_stream)
        handle = await manager.start(self.session.id, "等待")
        await asyncio.wait_for(entered.wait(), timeout=1)
        with self.assertRaises(RunConflictError):
            async with manager.session_mutation(self.session.id):
                pass
        await manager.cancel(handle.run_id)
        await asyncio.wait_for(handle.finished_event.wait(), timeout=2)

        async with manager.session_mutation(self.session.id):
            self.assertTrue(self.store.delete_session(self.session.id))


if __name__ == "__main__":
    unittest.main()
