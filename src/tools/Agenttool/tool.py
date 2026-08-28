from pathlib import Path
from src.context.message_context import (
    make_initial_state,
)
from pydantic import BaseModel, Field
from typing import Literal
from langgraph.errors import GraphRecursionError
import asyncio


TOOL_NAME = "agenttool"
IS_READ_ONLY = False
TOOL_DIR = Path(__file__).resolve().parent

DEFAULT_SUBAGENT_RECURSION_LIMIT = 200


class InputSchema(BaseModel):
    prompt: str = Field(description="交给子agent独立完成的任务描述")

class AgentResult(BaseModel):
    prompt: str = Field(description="主agent交给子agent的原始描述")
    answer: str = Field(description="子agent完成任务后的回答")
    status: Literal["completed", "partial"] = Field(
        description="任务是完整完成还是部分完成"
    )
    stop_reason: str = Field(
        default="",
        description="部分完成的原因",
    )
    message_count: int = Field(
        description="子 agent 状态中累计的消息数量"
    )

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    data: AgentResult | None = Field(default=None, description="子agent的执行结果")




def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()
    
def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        input_data = InputSchema(**kwargs)
    except Exception as error:
        return False, str(error)

    if not input_data.prompt.strip():
        return False, "prompt 不能为空。"

    return True, ""


def call(**kwargs) -> dict:
    return asyncio.run(acall(**kwargs))

async def acall(**kwargs) -> dict:
    profile_name = kwargs.pop("_profile_name", "qwen3.6")
    tool_names = kwargs.pop("_tool_names", None)
    vision_profile_name = kwargs.pop(
        "_vision_profile_name",
        "qwen3-vl",
    )
    working_dir = kwargs.pop("_working_dir", None)
    context_window_tokens = kwargs.pop(
        "_context_window_tokens",
        32768,
    )
    recursion_limit = kwargs.pop(
        "_recursion_limit",
        DEFAULT_SUBAGENT_RECURSION_LIMIT,
    )

    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)
        prompt = input_data.prompt.strip()

        from src.graphs.sub_agent_graph import build_graph

        graph = build_graph(
            profile_name=profile_name,
            vision_profile_name=vision_profile_name,
            working_dir=working_dir,
            context_window_tokens=context_window_tokens,
            tool_names=tool_names,
        )

        result = await graph.ainvoke(
            make_initial_state(
                prompt,
                turn_id=1,
            ),
            config={
                "recursion_limit": recursion_limit,
            },
        )

        messages = result.get("messages", [])
        last_message = messages[-1] if messages else None
        answer = (
            str(getattr(last_message, "content", "") or "")
            if last_message is not None
            else ""
        )

        status = result.get("status", "completed")
        stop_reason = result.get("stop_reason", "")

        return OutputSchema(
            ok=True,
            error="",
            data=AgentResult(
                prompt=prompt,
                answer=answer,
                status=status,
                stop_reason=stop_reason,
                message_count=len(messages),
            ),
        ).model_dump()

    except asyncio.CancelledError:
        raise

    except GraphRecursionError as error:
        return OutputSchema(
            ok=False,
            error=(
                "子 agent 达到硬递归限制，"
                "但没有成功生成阶段性结果："
                f"{error}"
            ),
            data=None,
        ).model_dump()

    except Exception as error:
        return OutputSchema(
            ok=False,
            error=str(error),
            data=None,
        ).model_dump()

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"子 agent 执行失败：{output.error}"

    if output.data is None:
        return "子 agent 执行成功，但没有返回结果。"

    if output.data.status == "partial":
        return (
            f"[子 agent 阶段性结果，原因："
            f"{output.data.stop_reason or '执行预算即将耗尽'}]\n"
            f"{output.data.answer}"
        )

    return output.data.answer
