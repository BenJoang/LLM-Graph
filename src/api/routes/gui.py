from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.graph_entrypoints import (
    DEFAULT_GRAPH_ENTRYPOINT,
    GraphEntrypointError,
    validate_graph_entrypoint,
)
from src.api.gui_auth import require_gui_token
from src.api.gui_messages import read_thread_messages
from src.api.gui_runtime import (
    RunConflictError,
    RunManager,
    SessionNotFoundError,
)
from src.api.gui_store import PROJECT_ROOT, GuiStore, load_safe_profiles
from src.persistence.checkpoints import delete_checkpoint_thread


router = APIRouter(
    prefix="/api",
    tags=["GUI"],
    dependencies=[Depends(require_gui_token)],
)
store = GuiStore()
run_manager = RunManager(store)


class SessionCreate(BaseModel):
    title: str = Field(default="新会话", max_length=120)
    profile_name: str | None = None
    vision_profile_name: str | None = None
    working_dir: str | None = None
    context_window_tokens: int = Field(default=32768, ge=1024, le=2_000_000)
    recursion_limit: int = Field(default=1000, ge=1, le=1000)
    graph_entrypoint: str = Field(
        default=DEFAULT_GRAPH_ENTRYPOINT,
        min_length=1,
        max_length=255,
    )


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    profile_name: str | None = None
    vision_profile_name: str | None = None
    working_dir: str | None = None
    context_window_tokens: int | None = Field(default=None, ge=1024, le=2_000_000)
    recursion_limit: int | None = Field(default=None, ge=1, le=1000)
    graph_entrypoint: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    archived: bool | None = None


class RunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=20000)


class GraphValidate(BaseModel):
    entrypoint: str = Field(min_length=1, max_length=255)


def _profile_names() -> set[str]:
    return {profile["name"] for profile in load_safe_profiles()}


def _defaults() -> tuple[str, str]:
    names = _profile_names()
    profile = "deepseekv4-flash" if "deepseekv4-flash" in names else next(iter(names))
    if "qwen3.8" in names:
        vision = "qwen3.8"
    elif "qwen3-vl" in names:
        vision = "qwen3-vl"
    else:
        vision = profile
    return profile, vision


def _validate_profile(name: str) -> str:
    if name not in _profile_names():
        raise HTTPException(status_code=422, detail=f"未知模型 Profile：{name}")
    return name


def _validate_working_dir(value: str | None) -> str:
    path = Path(value or PROJECT_ROOT).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail=f"工作目录不存在：{path}")
    return str(path)


def _validate_graph(value: str) -> str:
    try:
        return validate_graph_entrypoint(value)
    except GraphEntrypointError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _require_session(session_id: str):
    record = store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return record


@router.get("/profiles")
def profiles() -> dict:
    return {"profiles": load_safe_profiles()}


@router.post("/graphs/validate")
def validate_graph(request: GraphValidate) -> dict:
    return {"entrypoint": _validate_graph(request.entrypoint), "valid": True}


@router.get("/sessions")
def sessions(archived: bool = False) -> dict:
    return {"sessions": [item.to_dict() for item in store.list_sessions(archived)]}


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(request: SessionCreate) -> dict:
    default_profile, default_vision = _defaults()
    record = store.create_session(
        title=request.title,
        profile_name=_validate_profile(request.profile_name or default_profile),
        vision_profile_name=_validate_profile(
            request.vision_profile_name or default_vision
        ),
        working_dir=_validate_working_dir(request.working_dir),
        context_window_tokens=request.context_window_tokens,
        recursion_limit=request.recursion_limit,
        graph_entrypoint=_validate_graph(request.graph_entrypoint),
    )
    return {"session": record.to_dict()}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    record = _require_session(session_id)
    return {
        "session": record.to_dict(),
        "messages": read_thread_messages(session_id),
    }


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, request: SessionPatch) -> dict:
    current = _require_session(session_id)
    patch = request.model_dump(exclude_unset=True)
    if patch.get("profile_name") is not None:
        patch["profile_name"] = _validate_profile(patch["profile_name"])
    if patch.get("vision_profile_name") is not None:
        patch["vision_profile_name"] = _validate_profile(
            patch["vision_profile_name"]
        )
    if patch.get("working_dir") is not None:
        patch["working_dir"] = _validate_working_dir(patch["working_dir"])
    graph_changed = (
        patch.get("graph_entrypoint") is not None
        and patch["graph_entrypoint"].strip() != current.graph_entrypoint
    )
    if patch.get("graph_entrypoint") is not None:
        patch["graph_entrypoint"] = _validate_graph(
            patch["graph_entrypoint"]
        )

    guarded = graph_changed or "archived" in patch
    try:
        if guarded:
            async with run_manager.session_mutation(session_id):
                if graph_changed and await asyncio.to_thread(
                    read_thread_messages,
                    session_id,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="已有消息的会话不能更换 Graph",
                    )
                record = await asyncio.to_thread(
                    store.update_session,
                    session_id,
                    patch,
                )
        else:
            record = await asyncio.to_thread(
                store.update_session,
                session_id,
                patch,
            )
    except RunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": record.to_dict()}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    try:
        async with run_manager.session_mutation(session_id):
            await asyncio.to_thread(delete_checkpoint_thread, session_id)
            deleted = await asyncio.to_thread(store.delete_session, session_id)
    except RunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"永久删除失败：{type(error).__name__}: {error}",
        ) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "session_id": session_id}


@router.post("/sessions/{session_id}/runs")
async def run_session(
    session_id: str,
    payload: RunCreate,
    request: Request,
):
    record = _require_session(session_id)
    if record.archived:
        raise HTTPException(status_code=409, detail="归档会话不能运行")
    try:
        handle = await run_manager.start(session_id, payload.question.strip())
    except RunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    async def event_stream():
        try:
            async for event in handle.iter_sse():
                if await request.is_disconnected():
                    break
                yield event
        except asyncio.CancelledError:
            raise
        finally:
            if not handle.finished_event.is_set():
                await asyncio.shield(
                    run_manager.cancel(handle.run_id, wait=False)
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Run-ID": handle.run_id,
        },
    )


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(run_id: str) -> dict:
    handle = await run_manager.cancel(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return {
        "ok": True,
        "run_id": run_id,
        "status": handle.status,
        "already_finished": handle.finished_event.is_set(),
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    handle = await run_manager.get(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return {
        "run_id": run_id,
        "session_id": handle.session_id,
        "status": handle.status,
        "finished": handle.finished_event.is_set(),
    }
