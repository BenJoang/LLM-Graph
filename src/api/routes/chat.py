from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.client.mymodel_client import (
    build_async_client,
    chat_once_nothinking_async,
    load_profile,
    load_prompt,
)

router = APIRouter(prefix="/chat", tags=["Chat"])

class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)

@router.post("/once")
async def chat(request: ChatRequest) -> dict:
    
    profile = load_profile("qwen3.6")
    prompt = load_prompt("default")

    async with build_async_client(profile, timeout=180) as client:
        answer = await chat_once_nothinking_async(
            client,
            profile,
            prompt,
            request.question,
        )

    return {
        "ok": True,
        "answer": answer or "",
    }
