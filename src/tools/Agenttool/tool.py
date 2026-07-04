from pathlib import Path

from pydantic import BaseModel, Field


TOOL_NAME = "agenttool"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent


class InputSchema(BaseModel):
    prompt: str = Field(description="交给子agent独立完成的任务描述")

class AgentResult(BaseModel):
    prompt: str = Field(description="主agent交给子agent的原始描述")
    answer: str = Field(description="子agent完成任务后的回答")
    message_count: int = Field(description="子agent与模型交互的轮数")

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
        InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)

    return True, ""


def call(**kwargs) -> dict:
    try:
        profile_name = kwargs.pop("_profile_name", "qwen3.6")
        input_data = InputSchema(**kwargs)

        from src.graphs.sub_agent_graph import build_graph
        graph = build_graph(profile_name=profile_name)

        result = graph.invoke(
            {
                "messages":[
                    {
                        "role":"user",
                        "content": input_data.prompt,
                    }
                ]
            },
            config={"recursion_limit":100}
        )

        answer = result["messages"][-1].content

        return OutputSchema(
            ok=True,
            error="",
            data=AgentResult(
                prompt=input_data.prompt,
                answer=answer,
                message_count=len(result["messages"]),
            )
        ).model_dump()
    
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            data=None,
        ).model_dump()

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"工具执行失败：{output.error}"
    
    if output.data is None:
        return "子agent执行成功，但没有返回结果。"

    return output.data.answer
