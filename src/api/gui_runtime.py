from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.api.gui_messages import message_to_dto
from src.api.graph_entrypoints import resolve_graph_entrypoint
from src.api.gui_store import GuiStore, SessionRecord


RUN_TIMEOUT_SECONDS: float | None = None


class RunConflictError(RuntimeError):
    pass


class SessionNotFoundError(RuntimeError):
    pass


AgentStreamFactory = Callable[..., AsyncIterator[dict]]


async def _default_agent_stream(
    *,
    graph_entrypoint: str,
    **kwargs,
) -> AsyncIterator[dict]:
    stream_agent = resolve_graph_entrypoint(graph_entrypoint)
    async for update in stream_agent(**kwargs):
        yield update


@dataclass
class RunHandle:
    run_id: str
    session_id: str
    events: asyncio.Queue[dict | None] = field(default_factory=asyncio.Queue)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    finished_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    status: str = "pending"

    def emit(self, event_type: str, data: dict) -> None:
        self.events.put_nowait({"event": event_type, "data": data})

    def finish_stream(self) -> None:
        if self.finished_event.is_set():
            return
        self.finished_event.set()
        self.events.put_nowait(None)

    async def iter_sse(self) -> AsyncIterator[str]:
        while True:
            item = await self.events.get()
            if item is None:
                return
            payload = json.dumps(item["data"], ensure_ascii=False, default=str)
            yield f"event: {item['event']}\ndata: {payload}\n\n"


class RunManager:
    def __init__(
        self,
        store: GuiStore,
        *,
        stream_agent: AgentStreamFactory | None = None,
        run_timeout_seconds: float | None = RUN_TIMEOUT_SECONDS,
    ) -> None:
        self.store = store
        self._stream_agent = stream_agent
        self._run_timeout_seconds = run_timeout_seconds
        self._lock = asyncio.Lock()
        self._active: dict[str, RunHandle] = {}
        self._mutating: set[str] = set()
        self._runs: dict[str, RunHandle] = {}

    async def start(
        self,
        session_id: str,
        question: str,
    ) -> RunHandle:
        async with self._lock:
            if session_id in self._mutating:
                raise RunConflictError("会话正在修改，请稍后重试")
            active = self._active.get(session_id)
            if active is not None and not active.finished_event.is_set():
                raise RunConflictError("当前会话已有 Agent 任务正在运行")
            session = self.store.get_session(session_id)
            if session is None:
                raise SessionNotFoundError("会话不存在")
            if session.archived:
                raise RunConflictError("归档会话不能运行")

            handle = RunHandle(
                run_id=f"run-{uuid4().hex}",
                session_id=session.id,
            )
            handle.status = "running"
            handle.emit(
                "run.started",
                {
                    "run_id": handle.run_id,
                    "session_id": handle.session_id,
                },
            )
            self._active[session.id] = handle
            self._runs[handle.run_id] = handle
            handle.task = asyncio.create_task(
                self._worker(handle, session, question),
                name=handle.run_id,
            )
            handle.task.add_done_callback(
                lambda task: self._ensure_task_finalized(handle, task)
            )

        return handle

    async def is_session_active(self, session_id: str) -> bool:
        async with self._lock:
            handle = self._active.get(session_id)
            return handle is not None and not handle.finished_event.is_set()

    @asynccontextmanager
    async def session_mutation(self, session_id: str):
        """Serialize lifecycle mutations with starts for one session."""

        async with self._lock:
            active = self._active.get(session_id)
            if active is not None and not active.finished_event.is_set():
                raise RunConflictError("运行中的会话不能执行此操作，请先停止任务")
            if session_id in self._mutating:
                raise RunConflictError("会话正在修改，请稍后重试")
            if self.store.get_session(session_id) is None:
                raise SessionNotFoundError("会话不存在")
            self._mutating.add(session_id)
        try:
            yield
        finally:
            async with self._lock:
                self._mutating.discard(session_id)

    async def cancel(
        self,
        run_id: str,
        *,
        wait: bool = True,
    ) -> RunHandle | None:
        async with self._lock:
            handle = self._runs.get(run_id)

        if handle is None:
            return None
        if handle.finished_event.is_set():
            return handle

        handle.status = "cancelling"
        handle.cancel_event.set()
        if handle.task is not None and not handle.task.done():
            handle.task.cancel()

        if wait and handle.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(handle.task), timeout=3)
            except (asyncio.CancelledError, TimeoutError):
                pass

        return handle

    async def get(self, run_id: str) -> RunHandle | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def shutdown(self) -> None:
        async with self._lock:
            handles = list(self._runs.values())

        tasks: list[asyncio.Task[None]] = []
        for handle in handles:
            if handle.finished_event.is_set() or handle.task is None:
                continue
            handle.status = "cancelling"
            handle.cancel_event.set()
            handle.task.cancel()
            tasks.append(handle.task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker(
        self,
        handle: RunHandle,
        session: SessionRecord,
        question: str,
    ) -> None:
        calls: dict[str, dict[str, Any]] = {}

        try:
            await asyncio.to_thread(
                self.store.touch_with_question,
                session.id,
                question,
            )
            stream_agent = self._stream_agent or _default_agent_stream
            stream_kwargs = {
                "question": question,
                "thread_id": session.id,
                "profile_name": session.profile_name,
                "vision_profile_name": session.vision_profile_name,
                "recursion_limit": session.recursion_limit,
                "working_dir": session.working_dir,
                "context_window_tokens": session.context_window_tokens,
            }
            if self._stream_agent is None:
                stream_kwargs["graph_entrypoint"] = session.graph_entrypoint
            async with asyncio.timeout(self._run_timeout_seconds):
                async for update in stream_agent(**stream_kwargs):
                    self._emit_update(handle, update, calls)

            handle.status = "completed"
            record = await asyncio.to_thread(
                self.store.touch_with_question,
                session.id,
                question,
            )
            handle.emit(
                "run.completed",
                {
                    "run_id": handle.run_id,
                    "session_id": session.id,
                    "session": record.to_dict() if record else None,
                },
            )
        except asyncio.CancelledError:
            handle.status = "cancelled"
            handle.emit(
                "run.cancelled",
                {
                    "run_id": handle.run_id,
                    "session_id": session.id,
                },
            )
            raise
        except TimeoutError:
            handle.status = "timed_out"
            handle.emit(
                "run.timed_out",
                {
                    "run_id": handle.run_id,
                    "session_id": session.id,
                    "timeout_seconds": self._run_timeout_seconds,
                },
            )
        except Exception as error:
            handle.status = "error"
            handle.emit(
                "run.error",
                {
                    "run_id": handle.run_id,
                    "session_id": session.id,
                    "error": f"{type(error).__name__}: {error}",
                },
            )
        finally:
            handle.finish_stream()
            async with self._lock:
                if self._active.get(handle.session_id) is handle:
                    self._active.pop(handle.session_id, None)

    def _ensure_task_finalized(
        self,
        handle: RunHandle,
        task: asyncio.Task[None],
    ) -> None:
        """Handle cancellation before the worker coroutine starts running."""
        if handle.finished_event.is_set():
            return
        if task.cancelled():
            handle.status = "cancelled"
            handle.emit(
                "run.cancelled",
                {
                    "run_id": handle.run_id,
                    "session_id": handle.session_id,
                },
            )
        else:
            error = task.exception()
            handle.status = "error"
            handle.emit(
                "run.error",
                {
                    "run_id": handle.run_id,
                    "session_id": handle.session_id,
                    "error": (
                        f"{type(error).__name__}: {error}"
                        if error is not None
                        else "运行意外结束"
                    ),
                },
            )
        handle.finish_stream()
        asyncio.create_task(self._release_active(handle))

    async def _release_active(self, handle: RunHandle) -> None:
        async with self._lock:
            if self._active.get(handle.session_id) is handle:
                self._active.pop(handle.session_id, None)

    @staticmethod
    def _emit_update(
        handle: RunHandle,
        update: dict,
        calls: dict[str, dict[str, Any]],
    ) -> None:
        for node, payload in update.items():
            if not isinstance(payload, dict):
                continue
            for message in payload.get("messages", []) or []:
                dto = message_to_dto(message)
                if dto["role"] == "assistant":
                    handle.emit(
                        "assistant.step",
                        {"node": node, "message": dto},
                    )
                    for call in dto.get("tool_calls", []):
                        call_id = str(call.get("id") or uuid4().hex)
                        calls[call_id] = {
                            "name": call.get("name") or "tool",
                            "args": call.get("args") or {},
                            "started_at": time.monotonic(),
                        }
                        handle.emit(
                            "tool.started",
                            {
                                "call_id": call_id,
                                "name": calls[call_id]["name"],
                                "args": calls[call_id]["args"],
                            },
                        )
                if dto["role"] == "tool":
                    call_id = str(dto.get("tool_call_id") or "")
                    call = calls.get(call_id, {})
                    duration = None
                    if call.get("started_at") is not None:
                        duration = round(time.monotonic() - call["started_at"], 3)
                    handle.emit(
                        "tool.finished",
                        {
                            "call_id": call_id,
                            "name": dto.get("name") or call.get("name") or "tool",
                            "content": dto.get("content") or "",
                            "status": dto.get("status") or "success",
                            "duration_seconds": duration,
                        },
                    )
