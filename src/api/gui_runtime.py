from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.api.gui_messages import message_to_dto
from src.api.gui_store import GuiStore, SessionRecord
from src.graphs.tool_agent_graph import stream_tool_agent


class RunConflictError(RuntimeError):
    pass


@dataclass
class RunHandle:
    run_id: str
    session_id: str
    events: queue.Queue[dict | None] = field(default_factory=queue.Queue)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)

    def emit(self, event_type: str, data: dict) -> None:
        self.events.put({"event": event_type, "data": data})

    def finish_stream(self) -> None:
        self.finished_event.set()
        self.events.put(None)

    def iter_sse(self):
        while True:
            item = self.events.get()
            if item is None:
                return
            payload = json.dumps(item["data"], ensure_ascii=False, default=str)
            yield f"event: {item['event']}\ndata: {payload}\n\n"


class RunManager:
    def __init__(self, store: GuiStore) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._active: RunHandle | None = None
        self._runs: dict[str, RunHandle] = {}

    def start(self, session: SessionRecord, question: str) -> RunHandle:
        with self._lock:
            if self._active is not None and not self._active.finished_event.is_set():
                raise RunConflictError("已有 Agent 任务正在运行")
            handle = RunHandle(
                run_id=f"run-{uuid4().hex}",
                session_id=session.id,
            )
            self._active = handle
            self._runs[handle.run_id] = handle

        self.store.touch_with_question(session.id, question)
        threading.Thread(
            target=self._worker,
            args=(handle, session, question),
            daemon=True,
            name=f"gui-{handle.run_id[:12]}",
        ).start()
        return handle

    def cancel(self, run_id: str) -> RunHandle | None:
        with self._lock:
            handle = self._runs.get(run_id)
        if handle is not None and not handle.finished_event.is_set():
            handle.cancel_event.set()
        return handle

    def _worker(
        self,
        handle: RunHandle,
        session: SessionRecord,
        question: str,
    ) -> None:
        calls: dict[str, dict[str, Any]] = {}
        handle.emit(
            "run.started",
            {
                "run_id": handle.run_id,
                "session_id": handle.session_id,
            },
        )
        try:
            for update in stream_tool_agent(
                question=question,
                thread_id=session.id,
                profile_name=session.profile_name,
                vision_profile_name=session.vision_profile_name,
                recursion_limit=session.recursion_limit,
                working_dir=session.working_dir,
                context_window_tokens=session.context_window_tokens,
                should_cancel=handle.cancel_event.is_set,
            ):
                self._emit_update(handle, update, calls)

            if handle.cancel_event.is_set():
                handle.emit(
                    "run.cancelled",
                    {"run_id": handle.run_id, "session_id": session.id},
                )
            else:
                record = self.store.touch_with_question(session.id, question)
                handle.emit(
                    "run.completed",
                    {
                        "run_id": handle.run_id,
                        "session_id": session.id,
                        "session": record.to_dict() if record else None,
                    },
                )
        except Exception as error:
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
            with self._lock:
                if self._active is handle:
                    self._active = None

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
