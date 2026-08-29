from pathlib import Path
import argparse


TOOLS_DIR = Path(__file__).resolve().parent
EXECUTION_MODES = ("sync", "async")


def snake_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def create_prompt_md(tool_name: str) -> str:
    return f"""# {tool_name}

## DESCRIPTION
一句话描述这个工具的功能。
## PROMPT

### WHEN_TO_USE
当用户需要执行 `{tool_name}` 对应的能力时，使用这个工具。

### WHEN_NOT_TO_USE
说明这个工具不适合什么场景。

### INPUT_RULES
说明每个输入字段应该如何填写。

### LIMITS
说明工具限制、可能失败的原因，以及失败后模型应该怎么回答。
"""


def create_sync_execution_block() -> str:
    return '''def execute(input_data: InputSchema) -> object:
    """实现同步工具逻辑。"""
    return {
        "input": input_data.model_dump(),
        "message": "TODO: implement sync tool logic",
    }


def call(**kwargs) -> dict:
    input_data, failure = prepare_input(**kwargs)
    if failure is not None:
        return failure

    try:
        result_data = execute(input_data)
        return OutputSchema(
            ok=True,
            error="",
            data=result_data,
        ).model_dump()
    except Exception as error:
        return OutputSchema(
            ok=False,
            error=str(error),
            data=None,
        ).model_dump()


async def acall(**kwargs) -> dict:
    # 适用于短时间且没有原生异步 API 的同步工具。
    # 取消等待不会停止底层线程，因此 Shell、长任务和持续进程
    # 应使用 --execution async 生成原生异步实现。
    return await asyncio.to_thread(call, **kwargs)
'''


def create_async_execution_block() -> str:
    return '''async def execute(input_data: InputSchema) -> object:
    """实现原生异步工具逻辑。"""
    await asyncio.sleep(0)
    return {
        "input": input_data.model_dump(),
        "message": "TODO: implement async tool logic",
    }


async def acall(**kwargs) -> dict:
    input_data, failure = prepare_input(**kwargs)
    if failure is not None:
        return failure

    try:
        result_data = await execute(input_data)
        return OutputSchema(
            ok=True,
            error="",
            data=result_data,
        ).model_dump()
    except asyncio.CancelledError:
        # 取消必须继续向上传递；资源清理由 execute 的 finally 负责。
        raise
    except Exception as error:
        return OutputSchema(
            ok=False,
            error=str(error),
            data=None,
        ).model_dump()


def call(**kwargs) -> dict:
    """为同步 graph 提供兼容入口。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(acall(**kwargs))

    raise RuntimeError(
        f"{TOOL_NAME}.call() 不能在正在运行的事件循环中调用；"
        "请使用 await acall(...)"
    )
'''


def create_tool_py(
    tool_name: str,
    destructive: bool = False,
    execution: str = "sync",
) -> str:
    if execution not in EXECUTION_MODES:
        raise ValueError(
            f"execution 必须是以下值之一：{', '.join(EXECUTION_MODES)}"
        )

    is_read_only = "False" if destructive else "True"
    is_destructive = "True" if destructive else "False"
    execution_block = (
        create_async_execution_block()
        if execution == "async"
        else create_sync_execution_block()
    )

    return f'''import asyncio
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

TOOL_NAME = "{tool_name}"
TOOL_DIR = Path(__file__).resolve().parent

IS_READ_ONLY = {is_read_only}
IS_DESTRUCTIVE = {is_destructive}
MAX_RESULT_CHARS = 10000


class InputSchema(BaseModel):
    placeholder: str = Field(description="TODO: 描述输入字段")


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
    except ValidationError as error:
        return False, str(error)

    return True, ""


def check_permissions(**kwargs) -> tuple[bool, str]:
    """工具级权限检查。"""
    if IS_DESTRUCTIVE:
        return True, ""

    return True, ""


def prepare_input(**kwargs) -> tuple[InputSchema | None, dict | None]:
    """统一完成参数校验和权限检查，供 call/acall 复用。"""
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return None, OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    input_data = InputSchema(**kwargs)
    allowed, permission_error = check_permissions(
        **input_data.model_dump()
    )
    if not allowed:
        return None, OutputSchema(
            ok=False,
            error=permission_error,
            data=None,
        ).model_dump()

    return input_data, None


def summarize_input(**kwargs) -> str:
    """给日志、调试、模型中间态看的简短描述。"""
    try:
        input_data = InputSchema(**kwargs)
    except ValidationError:
        return f"{{TOOL_NAME}} input invalid"

    return f"Run {{TOOL_NAME}} with {{input_data.model_dump()}}"


def truncate_text(
    text: str,
    max_chars: int = MAX_RESULT_CHARS,
) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    return text[:max_chars], True


{execution_block}

def render_result_for_llm(result: dict) -> str:
    """把结构化结果转换成模型可读文本。"""
    output = OutputSchema(**result)

    if not output.ok:
        return f"{{TOOL_NAME}}工具执行失败：{{output.error}}"

    return f"{{TOOL_NAME}}工具执行成功：{{output.data}}"
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "tool_name",
        help="工具名，例如 get_file 或 read_word",
    )
    parser.add_argument(
        "--execution",
        choices=EXECUTION_MODES,
        default="sync",
        help="工具执行模式（默认：sync）",
    )
    parser.add_argument(
        "--destructive",
        action="store_true",
        help="将工具标记为可能产生副作用",
    )
    args = parser.parse_args()

    tool_name = args.tool_name.strip()
    folder_name = snake_to_pascal(tool_name)
    tool_dir = TOOLS_DIR / folder_name

    if tool_dir.exists():
        raise FileExistsError(f"工具目录已存在: {tool_dir}")

    tool_dir.mkdir(parents=True)
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "prompt.md").write_text(
        create_prompt_md(tool_name),
        encoding="utf-8",
    )
    (tool_dir / "tool.py").write_text(
        create_tool_py(
            tool_name,
            destructive=args.destructive,
            execution=args.execution,
        ),
        encoding="utf-8",
    )

    print(f"已创建工具脚手架: {tool_dir}")
    print("下一步：")
    print("1. 修改 prompt.md")
    print("2. 修改 tool.py 里的 InputSchema / execute")
    print("3. 在 registry.py 里手动 import 并注册这个 tool")


if __name__ == "__main__":
    main()
