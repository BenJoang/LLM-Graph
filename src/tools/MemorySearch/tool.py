from pathlib import Path
import yaml
from typing import Literal

from pydantic import BaseModel, Field


TOOL_NAME = "memory_search"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent

BASE_DIR = Path(__file__).resolve().parents[3]
MEMORY_ROOT = (BASE_DIR / "memory").resolve()


class InputSchema(BaseModel):
    file_path: str = Field(description="记忆文件绝对路径")
    mode: Literal["metadata", "list", "read"] = Field(default="metadata", description="metadata读取说明；list列出指定层级的子字段；read读取具体内容")
    field_path: list[str] = Field(default_factory=list, description="要访问的字段路径。例如 ['members', '1010475105', 'events']")
    cursor: int | None = Field(default=None, description="从第几条记录开始读取，默认为None表示从第一条记录开始")
    limit: int  = Field(default=20, description="最多读取多少条记录，默认读取20条记录，最多为100")

class SearchResult(BaseModel):
    file_path: str = Field(description="被搜索的记忆文件路径")
    mode: str = Field(description="搜索模式，metadata、list或read")
    field_path: list[str] = Field(description="被访问的字段路径")
    records: list[dict] = Field(description="搜索到的记录列表")
    has_more: bool = Field(description="是否有更多记录可以读取")
    next_cursor: int | None = Field(default=None, description="下一次搜索的起始位置，如果没有更多记录则为None")
    total_records: int = Field(description="记忆文件中的记录总数")

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    data: SearchResult | None = Field(default=None, description="返回的搜索结果，成功时包含SearchResult对象，失败时为None")



def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()

def resolve_memory_file(file_path: str) -> Path:
    return Path(file_path).expanduser().resolve()
    
def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        input_data = InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)
    
    path = resolve_memory_file(input_data.file_path)

    if not path.exists():
        return False, f"文件 '{input_data.file_path}' 不存在。"
    if not path.is_file():
        return False, f"路径 '{input_data.file_path}' 不是一个文件。"
    if path != MEMORY_ROOT and MEMORY_ROOT not in path.parents:
        return False, f"只允许读取memory目录中的文件:{path}"
    
    if input_data.cursor is not None and input_data.cursor < 1:
        return False, "cursor 必须大于或等于 1"

    if input_data.limit < 1 or input_data.limit > 100:
        return False, "limit 必须在 1 到 100 之间"

    for key in input_data.field_path:
        if not key:
            return False, "field_path 中不能包含空字段"

        if key in {".", ".."}:
            return False, "field_path 中包含不允许的字段"

    if input_data.mode == "read" and not input_data.field_path:
        return False, "read 模式必须提供 field_path"
    
    suffix = path.suffix.lower()
    allowed_suffixes = {".yaml", ".yml"}

    if suffix not in allowed_suffixes:
        return False, f"文件 '{input_data.file_path}' 的格式不被允许。"

    return True, ""

def load_yaml_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("YAML 顶层结构必须是 mapping")

    return data

def resolve_field(data: dict, field_path: list[str]):
    current = data

    

    for index, key in enumerate(field_path):

        if not isinstance(current, dict):
            raise ValueError(f"字段路径无法继续进入：{key}")
        if key not in current:
            parent_path = field_path[:index]
            raise ValueError(
                f"字段不存在：{'.'.join(field_path)}。"
                f"请先使用 mode='list', field_path={parent_path} "
                "查看已有字段，不要继续猜测字段名称。"
    )
        current = current[key]

    return current

def get_value_type(value) -> str:
    if isinstance(value, dict):
        return "mapping"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return "unknown"

def list_children(value, cursor: int | None, limit: int) -> list[dict]:
    start = (cursor or 1) - 1

    if isinstance(value, dict):
        items = list(value.items())[start:start + limit]

        return [
            {
                "key": str(key),
                "value_type": get_value_type(child),
                "has_children": isinstance(child, (dict, list)),
            }
            for key, child in items
        ]

    if isinstance(value, list):
        items = value[start:start + limit]

        return [
            {
                "index": start + index + 1,
                "value_type": get_value_type(child),
                "has_children": isinstance(child, (dict, list)),
            }
            for index, child in enumerate(items)
        ]

    raise ValueError("当前字段不是 mapping 或 list，不能继续列出子字段")

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

        path = resolve_memory_file(input_data.file_path)

        data = load_yaml_file(path)



        if input_data.mode == "metadata":
            metadata = data.get("metadata", {})
            records = [
                {
                    "field_path": ["metadata"],
                    "value": metadata
                }
            ]
            result_field_path = ["metadata"]
            total_records = 1
            next_cursor = None
            has_more = False
            
        elif input_data.mode == "list":
            value = resolve_field(data, input_data.field_path)

            if not isinstance(value, (dict, list)):
                raise ValueError("当前字段不是 mapping 或 list，不能继续列出子字段")
            
            total_records = len(value) 

            records = list_children(
                value=value,
                cursor=input_data.cursor,
                limit=input_data.limit,
            )
            start_index = (input_data.cursor or 1) - 1
            next_position = start_index + len(records)

            has_more = next_position < total_records
            result_field_path = input_data.field_path

            if has_more:
                next_cursor = next_position + 1
            else:
                next_cursor = None

            
        elif input_data.mode == "read":
            value = resolve_field(data, input_data.field_path)
            start_index = (input_data.cursor or 1) - 1
            if isinstance(value, dict):
                items = list(value.items())
                total_records = len(items)

                selected_items = items[
                    start_index : start_index + input_data.limit
                ]
                records = [
                    {
                        "key":str(key),
                        "value": child
                    }
                    for key, child in selected_items
                ]
            elif isinstance(value, list):
                total_records = len(value)

                selected_items = value[
                    start_index : start_index + input_data.limit
                ]
                records = [
                    {
                        "index": start_index + index + 1,
                        "value": child
                    }
                    for index, child in enumerate(selected_items)
                ]

            else:
                total_records = 1
                records = [
                    {
                        "value": value
                    }
                ]
            next_position = start_index + len(records)
            has_more = next_position < total_records
            
            result_field_path = input_data.field_path
            next_cursor = next_position + 1 if has_more else None
        return OutputSchema(
            ok=True,
            error="",
            data=SearchResult(
                file_path=str(path),
                mode=input_data.mode,
                field_path=result_field_path,
                records=records,
                has_more=has_more,
                next_cursor=next_cursor,
                total_records=total_records,
            ),
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

    return yaml.safe_dump(
        output.data.model_dump(),
        allow_unicode=True,
        sort_keys=False,
    )
