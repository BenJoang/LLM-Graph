import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.graphs.tool_agent_graph import run_tool_agent
from src.graphs.wuxiagent import run_tool_agent as wuxi_agent
from skills.wuxiwaterskill.src.server.check_latest_discharge_warning import (
    check_latest_discharge_warning,
)

router = APIRouter(prefix="/agent", tags=["Agent"])

class ToolAgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    profile_name: str = "qwen3.6"
    recursion_limit: int = 70
    working_dir: str | None = None

class LatestDischargeWarningRequest(BaseModel):
    hours: int = Field(default=2, ge=1, le=72)
    limit: int = Field(default=2000, ge=1, le=20000)
    min_level: int = Field(default=3, ge=1, le=5)

def extract_answer(result: Any) -> str:
    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        messages = result.get("messages") or []
        if messages:
            last = messages[-1]
            if isinstance(last, dict):
                return str(last.get("content", ""))
            return str(getattr(last, "content", ""))

    return str(result)

@router.post("/tool")
async def tool_agent(request: ToolAgentRequest) -> dict:
    result = await asyncio.to_thread(
        run_tool_agent,
        question=request.question,
        profile_name=request.profile_name,
        recursion_limit=request.recursion_limit,
        working_dir=request.working_dir,
    )

    return {
        "ok": True,
        "answer": extract_answer(result),
    }

@router.post("/wuxi-agent")
async def wuxi_agent_route(request: ToolAgentRequest) -> dict:
    result = await asyncio.to_thread(
        wuxi_agent,
        question=request.question,
        profile_name=request.profile_name,
        recursion_limit=request.recursion_limit,
        working_dir=request.working_dir,
    )

    return {
        "ok": True,
        "answer": extract_answer(result),
    }

@router.post("/latest-discharge-warning")
async def latest_discharge_warning(request: LatestDischargeWarningRequest) -> dict:
    result = await asyncio.to_thread(
        check_latest_discharge_warning,
        hours=request.hours,
        limit=request.limit,
        min_level=request.min_level,
    )

    return {
        "ok": True,
        **result,
    }