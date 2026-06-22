from pathlib import Path
from typing import Any, Literal
import yaml
from pydantic import BaseModel, Field
import json


TOOL_NAME = "memory_write"
IS_READ_ONLY = False
TOOL_DIR = Path(__file__).resolve().parent

BASE_DIR = Path(__file__).resolve().parents[3]
MEMORY_ROOT = (BASE_DIR / "memory").resolve()


class InputSchema(BaseModel):
    file_path: str = Field(description="需要修改的 YAML 记忆文件绝对路径")
    action: Literal["set", "append", "merge"] = Field(description="set替换字段；append向列表追加记录；merge向mapping合并字段")
    field_path: list[str] = Field(description="目标字段路径，例如 ['members', '1010475105', 'events']")
    value: Any = Field(description="需要写入的值，可以是字符串、列表或mapping")
    create_missing: bool = Field(default=False,description="是否允许创建不存在的字段，默认禁止")

class WriteResult(BaseModel):
    file_path: str = Field(description="被修改的记忆文件路径")
    field_path: list[str] = Field(description="被修改的字段路径")
    action: str = Field(description="执行的操作，set、append或merge")
    changed: bool = Field(description="是否实际修改了文件")
    message: str = Field(description="修改结果说明")

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    data: WriteResult | None = Field(default=None, description="工具返回的结构化数据")

class LiteralSafeDumper(yaml.SafeDumper):
    pass

def represent_str(dumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str",
            data,
            style="|",
        )

    return dumper.represent_scalar(
        "tag:yaml.org,2002:str",
        data,
    )

LiteralSafeDumper.add_representer(str, represent_str)

def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()
    
def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        input_data = InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)
    
    
    path = Path(input_data.file_path).expanduser().resolve()

    if not path.exists():
        return False, f"文件不存在：{path}"

    if not path.is_file():
        return False, f"目标不是文件：{path}"

    if MEMORY_ROOT not in path.parents:
        return False, "只允许修改 memory 目录中的文件"

    if path.suffix.lower() not in {".yaml", ".yml"}:
        return False, "只允许修改 YAML 文件"

    if not input_data.field_path:
        return False, "必须提供 field_path，禁止直接覆盖整个文件"

    for key in input_data.field_path:
        if not key or key in {".", ".."}:
            return False, "field_path 包含不允许的字段"

    return True, ""

def normalize_value(value):
    if not isinstance(value, str):
        return value

    text = value.strip()

    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return value

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value

def resolve_parent(data: dict, field_path: list[str], create_missing: bool):
    current = data

    for key in field_path[:-1]:
        if not isinstance(current, dict):
            raise ValueError(f"无法继续进入字段：{key}")

        if key not in current:
            if not create_missing:
                raise ValueError(f"字段不存在：{key}")
            current[key] = {}

        current = current[key]

    if not isinstance(current, dict):
        raise ValueError("目标字段的父级不是 mapping，无法写入")

    return current, field_path[-1]

def load_yaml_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("YAML 顶层结构必须是 mapping")

    return data

def save_yaml_file(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            Dumper=LiteralSafeDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

def call(**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()
    
    try:
        input_data = InputSchema(**kwargs)
        path = Path(input_data.file_path).expanduser().resolve()
        data = load_yaml_file(path)

        value = normalize_value(input_data.value)

        parent, key = resolve_parent(
            data,
            input_data.field_path,
            input_data.create_missing,
        )

        # 在这里放 set / append / merge 三个分支
        if input_data.action == "set":
            if key not in parent and not input_data.create_missing:
                raise ValueError(f"字段不存在：{key}")

            changed = parent.get(key) != value
            parent[key] = value

        elif input_data.action == "append":
            if key not in parent:
                if not input_data.create_missing:
                    raise ValueError(f"字段不存在：{key}")
                parent[key] = []

            if not isinstance(parent[key], list):
                raise ValueError("append 只能用于 list 字段")

            if value in parent[key]:
                changed = False
            else:
                parent[key].append(value)
                changed = True

        elif input_data.action == "merge":
            if key not in parent:
                if not input_data.create_missing:
                    raise ValueError(f"字段不存在：{key}")
                parent[key] = {}

            if not isinstance(parent[key], dict):
                raise ValueError("merge 只能用于 mapping 字段")

            if not isinstance(value, dict):
                raise ValueError("merge 的 value 必须是 mapping")

            before = parent[key].copy()
            parent[key].update(value)
            changed = before != parent[key]

        if changed:
            save_yaml_file(path, data)

        return OutputSchema(
            ok=True,
            data=WriteResult(
                file_path=str(path),
                action=input_data.action,
                field_path=input_data.field_path,
                changed=changed,
                message="修改成功" if changed else "内容已存在，无需重复写入",
            ),
        ).model_dump()

    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
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
        return f"工具执行失败：{output.error}"

    return f"工具执行成功：{output.data}"
