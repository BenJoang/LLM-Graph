from pathlib import Path

from docx import Document
from pydantic import BaseModel, Field


TOOL_NAME = "read_file"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent

DEFAULT_LIMIT = 120
MAX_LIMIT = 200
MAX_CHARS = 10000

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt"}
DOCX_SUFFIXES = {".docx"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | DOCX_SUFFIXES


class InputSchema(BaseModel):
    file_path: str = Field(description="要读取的文件的绝对路径，支持.docx和.py。")
    offset: int = Field(default=1, description="从第几行开始读取，默认从1开始")
    limit: int | None = Field(default=None, description="最多读取多少行，文件较长时使用，默认为None")

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    file_path: str = Field(default="", description="被读取的文件路径")
    file_type: str = Field(default="", description="被读取的文件类型，例如 'docx' 或 'py'")
    content: str = Field(default="", description="读取到的文件内容")
    start: int | None = Field(default=None, description="实际开始读取的行号")
    count: int | None = Field(default=None, description="实际读取的行数")
    total: int | None = Field(default=None, description="文件的总行数")
    truncated: bool = Field(default=False, description="如果文件内容超过限制，是否被截断")

def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()

def validate_input(file_path: str) -> tuple[bool, str]:

    try:
        input_data = InputSchema(file_path=file_path)
    except Exception as e:
        return False, str(e)
    
    path = Path(input_data.file_path)

    if not path.exists():
        return False, f"文件 '{input_data.file_path}' 不存在。"
    
    if not path.is_file():
        return False, f"路径 '{input_data.file_path}' 不是一个文件。"
    
    allowed_suffixes = {".docx", ".py", ".ts", ".tsx"}
    
    if path.suffix.lower() not in allowed_suffixes:
        return False, f"仅支持 {', '.join(allowed_suffixes)} 格式的文件。"
    
    return True, ""

def call(file_path: str, offset: int = 1, limit: int | None = None) -> dict:
    ok, error_message = validate_input(file_path)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            content=""
        ).model_dump()
    
    input_data = InputSchema(
        file_path=file_path, 
        offset=offset, 
        limit=limit
    )

    path = Path(input_data.file_path)

    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        return read_text_file(path, input_data.offset, input_data.limit)
    elif suffix in DOCX_SUFFIXES:
        return read_docx_file(path, input_data.offset, input_data.limit)
    
    return OutputSchema(
        ok=False,
        error=f"不支持的文件类型：{suffix}",
    ).model_dump()

def read_docx_file(path: Path, offset: int, limit: int | None) -> dict:
    doc = Document(path)

    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)

    total = len(paragraphs)
    start_index = offset - 1

    if limit is None:
        limit = DEFAULT_LIMIT
    
    if limit > MAX_LIMIT:
        limit = MAX_LIMIT

    selected = paragraphs[start_index : start_index + limit]

    content = "\n".join(
        f"{i}: {text}"
        for i, text in enumerate(selected, start=offset)
    )

    truncated_by_chars = False

    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS]
        truncated_by_chars = True

    has_more = start_index + len(selected) < total

    truncated = truncated_by_chars or has_more

    return OutputSchema(
        ok=True,
        error="",
        file_path=str(path),
        file_type=".docx",
        content=content,
        start=offset,
        count=len(selected),
        total=total,
        truncated=truncated,
    ).model_dump()

def read_text_file(path: Path, offset: int, limit: int | None) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    total = len(lines)
    start_index = offset - 1

    if limit is None:
        limit = DEFAULT_LIMIT

    if limit > MAX_LIMIT:
        limit = MAX_LIMIT

    selected = lines[start_index : start_index + limit]

    content = "\n".join(
        f"{i}: {line}"
        for i, line in enumerate(selected, start=offset)
    )

    truncated_by_chars = False
    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS]
        truncated_by_chars = True

    has_more = start_index + len(selected) < total

    truncated = truncated_by_chars or has_more

    return OutputSchema(
        ok=True,
        error="",
        file_path=str(path),
        file_type=path.suffix.lower(),
        content=content,
        start=offset,
        count=len(selected),
        total=total,
        truncated=truncated,
    ).model_dump()

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"读取文件失败，错误信息：{output.error}"

    return (f"已读取内容如下：\n{output.content}\n"
            f"读取了 {output.count} 行，从第 {output.start} 行开始，文件总行数为 {output.total}。\n"
            f"{'内容被截断了，因为超过限制。继续完整分析请继续调用该方法' if output.truncated else ''}")