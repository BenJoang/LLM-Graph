from pathlib import Path

from pydantic import BaseModel, Field

TOOL_NAME = "python_tool"
TOOL_DIR = Path(__file__).resolve().parent

IS_READ_ONLY = False
IS_DESTRUCTIVE = False
MAX_RESULT_CHARS = 10000

DEFAULT_TIME_OUT = 30
MAX_TIMEOUT = 120

class InputSchema(BaseModel):
    script_path: str = Field(description="目标项目根目录的绝对路径，必须是 .py文件")
    cwd: str | None = Field(default=None, description="执行脚本时的工作目录，默认脚本所在目录")
    args: list[str] = Field(default_factory=list,description="run_script 时传给脚本的参数",)

    python_path: str | None = Field(
        default=None,
        description="指定用于执行脚本的 Python 解释器路径，例如 .venv/Scripts/python.exe；不填则使用当前系统默认 Python",
    )
    timeout: int = Field(default=30, description="超时时间，单位秒")

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    data: object | None = Field(default=None, description="工具返回的结构化数据")


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

def check_permissions(**kwargs) -> tuple[bool, str]:
    """
    工具级权限检查。
    """
    if IS_DESTRUCTIVE:
        return True, ""

    return True, ""

def summarize_input(**kwargs) -> str:
    """
    给日志、调试、模型中间态看的简短描述。
    """
    try:
        input_data = InputSchema(**kwargs)
    except ValidationError:
        return f"{TOOL_NAME} input invalid"

    return f"Run {TOOL_NAME} with {input_data.model_dump()}"

def truncate_text(text: str, max_chars: int = MAX_RESULT_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    return text[:max_chars], True


def call(**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    allowed, permission_error = check_permissions(**kwargs)

    if not allowed:
        return OutputSchema(
            ok=False,
            error=permission_error,
            data=None,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)

        # TODO: 在这里实现工具逻辑。
        
        result_data = {
            "input": input_data.model_dump(),
            "message": "TODO: implement tool logic",
        }

        return OutputSchema(
            ok=True,
            error="",
            data=result_data,
        ).model_dump()
        
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            data=None,
        ).model_dump()

def render_result_for_llm(result: dict) -> str:
    """
    把结构化结果转换成模型可读文本。
    """
    output = OutputSchema(**result)

    if not output.ok:
        return f"{TOOL_NAME}工具执行失败：{output.error}"

    return f"{TOOL_NAME}工具执行成功：{output.data}"
