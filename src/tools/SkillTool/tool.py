from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

TOOL_NAME = "skill_tool"
TOOL_DIR = Path(__file__).resolve().parent

SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"

IS_READ_ONLY = True
IS_DESTRUCTIVE = False
MAX_RESULT_CHARS = 10000

class InputSchema(BaseModel):
    skill_name: str = Field(description="需要执行的skill名称")

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
        input_data = InputSchema(**kwargs)
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

def resolve_skill_md(skill_name: str) -> Path:
    name = skill_name.strip()

    if not name:
        raise ValueError("skill_name 不能为空")

    if "\\" in name or "/" in name:
        raise ValueError("skill_name 只能是技能目录名，不能包含路径分隔符")

    if name in {".", ".."}:
        raise ValueError("skill_name 不合法")

    skill_dir = (SKILLS_DIR / name).resolve()
    skills_root = SKILLS_DIR.resolve()

    if not skill_dir.is_relative_to(skills_root):
        raise ValueError("skill_name 不允许跳出 skills 目录")

    skill_md = skill_dir / "skill.md"

    if not skill_dir.is_dir():
        raise ValueError(f"skill 目录不存在：{skill_dir}")

    if not skill_md.is_file():
        raise ValueError(f"skill.md 不存在：{skill_md}")

    return skill_md


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

        skill_md = resolve_skill_md(input_data.skill_name)
        content = skill_md.read_text(encoding="utf-8")

        truncated_content, truncated = truncate_text(content)

        result_data = {
            "skill_name": input_data.skill_name,
            "skill_dir": str(skill_md.parent),
            "skill_md": str(skill_md),
            "content": truncated_content,
            "truncated": truncated,
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

    data = output.data if isinstance(output.data, dict) else {}
    skill_name = data.get("skill_name", "")
    skill_md = data.get("skill_md", "")
    content = data.get("content", "")
    truncated = data.get("truncated", False)

    lines = [
        f"已读取 skill：{skill_name}",
        f"路径：{skill_md}",
        "",
        content,
    ]

    if truncated:
        lines.append("")
        lines.append("注意：内容过长，已截断。")

    return "\n".join(lines)
