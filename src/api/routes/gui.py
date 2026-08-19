from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.gui_auth import require_gui_token
from src.api.gui_messages import read_thread_messages
from src.api.gui_runtime import RunConflictError, RunManager
from src.api.gui_store import PROJECT_ROOT, GuiStore, load_safe_profiles


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
    recursion_limit: int = Field(default=200, ge=1, le=1000)


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    profile_name: str | None = None
    vision_profile_name: str | None = None
    working_dir: str | None = None
    context_window_tokens: int | None = Field(default=None, ge=1024, le=2_000_000)
    recursion_limit: int | None = Field(default=None, ge=1, le=1000)
    archived: bool | None = None


class RunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=20000)


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


def _require_session(session_id: str):
    record = store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return record


@router.get("/profiles")
def profiles() -> dict:
    return {"profiles": load_safe_profiles()}


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
def patch_session(session_id: str, request: SessionPatch) -> dict:
    _require_session(session_id)
    patch = request.model_dump(exclude_unset=True)
    if patch.get("profile_name") is not None:
        patch["profile_name"] = _validate_profile(patch["profile_name"])
    if patch.get("vision_profile_name") is not None:
        patch["vision_profile_name"] = _validate_profile(
            patch["vision_profile_name"]
        )
    if patch.get("working_dir") is not None:
        patch["working_dir"] = _validate_working_dir(patch["working_dir"])
    record = store.update_session(session_id, patch)
    return {"session": record.to_dict()}


@router.post("/sessions/{session_id}/runs")
def run_session(session_id: str, request: RunCreate):
    record = _require_session(session_id)
    if record.archived:
        raise HTTPException(status_code=409, detail="归档会话不能运行")
    try:
        handle = run_manager.start(record, request.question.strip())
    except RunConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return StreamingResponse(
        handle.iter_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: str) -> dict:
    handle = run_manager.cancel(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return {
        "ok": True,
        "run_id": run_id,
        "already_finished": handle.finished_event.is_set(),
    }
