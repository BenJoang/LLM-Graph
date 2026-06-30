import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.graphs.tool_agent_graph import run_tool_agent

router = APIRouter(prefix="/agent", tags=["Agent"])

class ToolAgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    profile_name: str = "qwen3.6"
    recursion_limit: int = 70
    working_dir: str | None = None

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