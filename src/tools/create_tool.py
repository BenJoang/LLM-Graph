from pathlib import Path
import argparse


TOOLS_DIR = Path(__file__).resolve().parent

def snake_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))

def create_prompt_md(tool_name: str) -> str:
    return f"""# {tool_name}

## DESCRIPTION
简短描述这个工具的功能。
## PROMPT
当用户需要执行 `{tool_name}` 对应的能力时，使用这个工具。
说明这个工具适合什么场景、不适合什么场景，以及关键限制。
"""

def create_tool_py(tool_name: str) -> str:
    return f'''from pathlib import Path

from pydantic import BaseModel, Field


TOOL_NAME = "{tool_name}"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent


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
    except Exception as e:
        return False, str(e)

    return True, ""


def call(**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    return OutputSchema(
        ok=True,
        error="",
        data=None,
    ).model_dump()

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"工具执行失败：{{output.error}}"

    return f"工具执行成功：{{output.data}}"
'''

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tool_name", help="工具名，例如 get_file 或 read_word")
    args = parser.parse_args()

    tool_name = args.tool_name.strip()
    folder_name = snake_to_pascal(tool_name)
    tool_dir = TOOLS_DIR / folder_name

    if tool_dir.exists():
        raise FileExistsError(f"工具目录已存在: {tool_dir}")

    tool_dir.mkdir(parents=True)
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "prompt.md").write_text(create_prompt_md(tool_name), encoding="utf-8")
    (tool_dir / "tool.py").write_text(create_tool_py(tool_name), encoding="utf-8")

    print(f"已创建工具脚手架: {tool_dir}")
    print("下一步：")
    print("1. 修改 prompt.md")
    print("2. 修改 tool.py 里的 InputSchema / call")
    print("3. 在 registry.py 里手动 import 并注册这个 tool")

if __name__ == "__main__":
    main()