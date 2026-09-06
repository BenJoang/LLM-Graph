from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from src.context.context_compression import (
    MessageManage,
    make_empty_compression_session,
)
from src.context.message_context import set_context_state
from src.persistence.conversation_store import (
    ConversationStore,
    RunStateConflictError,
    create_conversation_store,
)


logger = logging.getLogger(__name__)

GraphUpdate = dict[str, Any]
UpdateCallback = Callable[[GraphUpdate], Awaitable[None] | None]
GraphStream = Callable[..., AsyncIterator[GraphUpdate]]

TOOL_AGENT_ENTRYPOINT = "src.graphs.tool_agent_graph:astream_tool_agent"
TTS_ENTRYPOINT = "src.graphs.tts_graph:astream_tool_agent"
MANAGED_GRAPH_BUILDERS: dict[str, tuple[str, str]] = {
    TOOL_AGENT_ENTRYPOINT: ("src.graphs.tool_agent_graph", "build_graph"),
    TTS_ENTRYPOINT: ("src.graphs.tts_graph", "build_graph"),
}


def is_managed_graph_entrypoint(entrypoint: str) -> bool:
    return entrypoint in MANAGED_GRAPH_BUILDERS


def load_graph_builder(entrypoint: str):
    try:
        module_name, attribute_name = MANAGED_GRAPH_BUILDERS[entrypoint]
    except KeyError as exc:
        raise ValueError(f"未注册或不可用的 Graph：{entrypoint}") from exc
    module = importlib.import_module(module_name)
    builder = getattr(module, attribute_name, None)
    if not callable(builder):
        raise ValueError(f"Graph builder 不可调用：{entrypoint}")
    return builder


async def stream_managed_graph(
    *,
    graph_entrypoint: str,
    initial_state: dict[str, Any],
    profile_name: str,
    vision_profile_name: str,
    recursion_limit: int,
    working_dir: str | None,
    context_window_tokens: int,
) -> AsyncIterator[GraphUpdate]:
    """构建无 checkpointer Graph，并输出节点级完整更新。"""

    builder = load_graph_builder(graph_entrypoint)
    graph = builder(
        profile_name=profile_name,
        vision_profile_name=vision_profile_name,
        working_dir=working_dir,
        checkpointer=None,
        context_window_tokens=context_window_tokens,
    )
    async for update in graph.astream(
        initial_state,
        config={"recursion_limit": recursion_limit},
        stream_mode="updates",
    ):
        yield update


def _node_outputs(update: GraphUpdate):
    for output in update.values():
        if isinstance(output, dict):
            yield output


def _messages_from_update(update: GraphUpdate):
    for output in _node_outputs(update):
        messages = output.get("messages") or []
        if isinstance(messages, BaseMessage):
            yield messages
        elif isinstance(messages, (list, tuple)):
            for message in messages:
                if isinstance(message, BaseMessage):
                    yield message


def _merge_update(state: dict[str, Any], update: GraphUpdate) -> None:
    for output in _node_outputs(update):
        for key, value in output.items():
            if key == "messages":
                state["messages"] = add_messages(
                    state.get("messages", []),
                    value or [],
                )
            else:
                state[key] = value


class ToolAgentRunner:
    """所有受管 Graph 入口共用的 SQL 对话运行协调器。"""

    def __init__(
        self,
        *,
        store: ConversationStore | None = None,
        graph_stream: GraphStream = stream_managed_graph,
    ) -> None:
        self.store = store or create_conversation_store()
        self._graph_stream = graph_stream
        self._setup_lock = asyncio.Lock()
        self._is_setup = False

    async def setup(self) -> None:
        if self._is_setup:
            return
        async with self._setup_lock:
            if self._is_setup:
                return
            await asyncio.to_thread(self.store.setup)
            recovered = await asyncio.to_thread(
                self.store.interrupt_running_runs,
                reason="process restarted",
            )
            if recovered:
                logger.warning(
                    "启动时将 %s 个遗留 running Run 标记为 interrupted",
                    recovered,
                )
            self._is_setup = True

    async def close(self) -> None:
        await asyncio.to_thread(self.store.close)
        self._is_setup = False

    async def _finish_cancelled(self, run_id: str) -> None:
        try:
            await asyncio.to_thread(
                self.store.cancel_run,
                run_id,
                reason="user cancelled",
            )
        except RunStateConflictError:
            logger.info("取消到达时 Run 已进入终态：%s", run_id)
        except Exception:
            logger.exception("标记 cancelled Run 失败：%s", run_id)

    async def _finish_failed(
        self,
        run_id: str,
        error: BaseException,
    ) -> None:
        error_text = f"{type(error).__name__}: {error}"[:4000]
        try:
            await asyncio.to_thread(
                self.store.fail_run,
                run_id,
                error=error_text,
            )
        except RunStateConflictError:
            logger.info("失败到达时 Run 已进入终态：%s", run_id)
        except Exception:
            logger.exception("标记 failed Run 失败：%s", run_id)

    async def execute(
        self,
        *,
        question: str,
        session_id: str,
        profile_name: str = "qwen3.6",
        vision_profile_name: str = "qwen3-vl",
        recursion_limit: int = 200,
        working_dir: str | None = None,
        context_window_tokens: int = 32768,
        graph_entrypoint: str = TOOL_AGENT_ENTRYPOINT,
        run_id: str | None = None,
        on_update: UpdateCallback | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """执行一轮；节点消息写入 SQL 后才通知流式调用方。"""

        if not is_managed_graph_entrypoint(graph_entrypoint):
            raise ValueError(f"未注册或不可用的 Graph：{graph_entrypoint}")
        await self.setup()
        requested_run_id = run_id or f"run-{uuid4().hex}"
        begin_task = asyncio.create_task(
            asyncio.to_thread(
                self.store.begin_run,
                session_id,
                run_id=requested_run_id,
            )
        )
        try:
            run = await asyncio.shield(begin_task)
        except asyncio.CancelledError as cancelled:
            try:
                run = await asyncio.shield(begin_task)
            except Exception:
                raise cancelled
            await asyncio.shield(self._finish_cancelled(run.run_id))
            raise

        async def execute_started_run() -> dict[str, Any]:
            context = await asyncio.to_thread(
                self.store.load_context,
                session_id,
            )
            if context is None:
                history = await asyncio.to_thread(
                    self.store.load_messages,
                    session_id,
                )
                compression_session: dict[str, Any] = (
                    make_empty_compression_session()
                )
            else:
                history = list(context.projected_messages)
                compression_session = dict(context.compression_session)

            user_message = HumanMessage(
                content=question,
                id=f"{run.run_id}:user",
            )
            set_context_state(user_message, turn_id=run.turn_no)
            user_event = await asyncio.to_thread(
                self.store.append_message,
                run.run_id,
                user_message,
            )
            persisted_ids = {
                str(message.id)
                for message in history
                if getattr(message, "id", None)
            }
            persisted_ids.add(str(user_message.id))

            state: dict[str, Any] = {
                "messages": [*history, user_message],
                "turn_id": run.turn_no,
                "compression_session": compression_session,
            }
            initial_state = {
                **state,
                "messages": list(state["messages"]),
            }
            next_ordinal = user_event.ordinal + 1

            async for update in self._graph_stream(
                graph_entrypoint=graph_entrypoint,
                initial_state=initial_state,
                profile_name=profile_name,
                vision_profile_name=vision_profile_name,
                recursion_limit=recursion_limit,
                working_dir=working_dir,
                context_window_tokens=context_window_tokens,
            ):
                for message in _messages_from_update(update):
                    if message.id is None:
                        message.id = f"{run.run_id}:message:{next_ordinal}"
                    message_id = str(message.id)
                    if message_id in persisted_ids:
                        continue
                    event = await asyncio.to_thread(
                        self.store.append_message,
                        run.run_id,
                        message,
                    )
                    persisted_ids.add(message_id)
                    next_ordinal = max(next_ordinal, event.ordinal + 1)

                _merge_update(state, update)
                if on_update is not None:
                    callback_result = on_update(update)
                    if inspect.isawaitable(callback_result):
                        await callback_result

            manager = MessageManage(max_tokens=context_window_tokens)
            projected, committed_compression = (
                manager.project_committed_context(
                    list(state.get("messages", [])),
                    state.get("compression_session"),
                )
            )
            state["compression_session"] = committed_compression
            commit_task = asyncio.create_task(
                asyncio.to_thread(
                    self.store.commit_run,
                    run.run_id,
                    projected_messages=projected,
                    compression_session=committed_compression,
                )
            )
            try:
                await asyncio.shield(commit_task)
            except asyncio.CancelledError:
                # completed/context 是同一事务的提交边界；事务已经启动后，
                # 让提交结果获胜，调用方也应看到 completed。
                await asyncio.shield(commit_task)
            return state

        try:
            if timeout_seconds is None:
                return await execute_started_run()
            return await asyncio.wait_for(
                execute_started_run(),
                timeout=timeout_seconds,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._finish_cancelled(run.run_id))
            raise
        except Exception as error:
            await self._finish_failed(run.run_id, error)
            raise

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        return await self.execute(**kwargs)

    async def astream(self, **kwargs: Any) -> AsyncIterator[GraphUpdate]:
        """旧生成器 API 的队列适配；执行仍只经过 execute。"""

        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()

        async def on_update(update: GraphUpdate) -> None:
            await queue.put(update)

        async def worker() -> dict[str, Any]:
            try:
                return await self.execute(**kwargs, on_update=on_update)
            finally:
                await queue.put(sentinel)

        task = asyncio.create_task(worker())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item  # type: ignore[misc]
            await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


_default_runner: ToolAgentRunner | None = None


def get_default_tool_agent_runner() -> ToolAgentRunner:
    global _default_runner
    if _default_runner is None:
        _default_runner = ToolAgentRunner()
    return _default_runner
