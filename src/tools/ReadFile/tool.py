from pathlib import Path

from docx import Document
from pydantic import BaseModel, Field


TOOL_NAME = "read_file"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent

DEFAULT_LIMIT = 120
MAX_LIMIT = 200
DEFAULT_MAX_CHARS = 4000
MAX_CHARS = 10000

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt"}
DOCX_SUFFIXES = {".docx"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | DOCX_SUFFIXES


class InputSchema(BaseModel):
    file_path: str = Field(description="要读取的文件的绝对路径，支持 .docx、.py、.ts、.tsx、.js、.jsx、.json、.md、.txt。")
    offset: int = Field(default=1, ge=1,description="从第几行开始读取，默认从1开始")
    char_offset: int = Field(
        default=0,
        ge=0,
        description="开始行内字符位置，从 0 开始",
    )
    limit: int | None = Field(default=None, ge=1,le=MAX_LIMIT,description="最多读取多少行，文件较长时使用，默认为None，上限为200")
    max_chars: int = Field(
        default=DEFAULT_MAX_CHARS,
        ge=500,
        le=MAX_CHARS,
        description="本次 content 最多返回多少字符",
    )


class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    file_path: str = Field(default="", description="被读取的文件路径")
    file_type: str = Field(default="", description="被读取的文件类型，例如 'docx' 或 'py'")
    content: str = Field(default="", description="读取到的文件内容")

    start: int | None = Field(default=None, description="本次开始读取的行号")
    start_char: int = Field(default=0,description="本次开始行内字符位置")

    end: int | None = Field(default=None,description="本次最后触及的行号")
    end_char: int = Field(default=0,description="最后一行读取结束的字符位置"
                          )
    count: int | None = Field(default=None, description="本次读取的行数")
    total: int | None = Field(default=None, description="文件的总行数")
    next_offset: int | None = Field(
        default=None,
        description="继续读取时应使用的行号；没有后续内容时为空",
    )
    next_char_offset: int = Field(
        default=0,
        description="继续读取时应使用的行内字符位置",
    )

    truncated: bool = Field(
        default=False,
        description="是否还有内容没有返回",
    )
    truncated_by_chars: bool = Field(
        default=False,
        description="是否因为字符数达到上限而停止",
    )
    truncated_by_lines: bool = Field(
        default=False,
        description="是否因为行数达到上限而停止",
    )

def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()

def validate_input(**kwargs) -> tuple[bool, str]:

    try:
        input_data = InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)
    
    path = Path(input_data.file_path)

    if not path.exists():
        return False, f"文件 '{input_data.file_path}' 不存在。"
    
    if not path.is_file():
        return False, f"路径 '{input_data.file_path}' 不是一个文件。"
    
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return False, f"仅支持 {', '.join(ALLOWED_SUFFIXES)} 格式的文件。"
    
    return True, ""

def call(
    file_path: str,
    offset: int = 1,
    char_offset: int = 0,
    limit: int | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict:
    ok, error_message = validate_input(
        file_path=file_path,
        offset=offset,
        char_offset=char_offset,
        limit=limit,
        max_chars=max_chars
    )
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            content=""
        ).model_dump()
    
    input_data = InputSchema(
        file_path=file_path,
        offset=offset,
        char_offset=char_offset,
        limit=limit,
        max_chars=max_chars
    )

    path = Path(input_data.file_path)

    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            return read_text_file(
                path,
                input_data.offset,
                input_data.char_offset,
                input_data.limit,
                max_chars=input_data.max_chars,
            )

        if suffix in DOCX_SUFFIXES:
            return read_docx_file(
                path,
                input_data.offset,
                input_data.char_offset,
                input_data.limit,
                max_chars=input_data.max_chars,
            )
        
        return OutputSchema(
            ok=False,
            error=f"不支持的文件类型：{suffix}",
        ).model_dump()
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=f"读取文件失败：{e}",
            file_path=str(path),
            file_type=suffix,
            content="",
        ).model_dump()
def read_numbered_lines(
    lines: list[str],
    *,
    offset: int,
    char_offset: int,
    limit: int | None,
    max_chars: int,
) -> dict:
    total = len(lines)
    effective_limit = min(
        limit if limit is not None else DEFAULT_LIMIT,
        MAX_LIMIT,
    )

    start_index = offset - 1

    if start_index >= total:
        return {
            "content": "",
            "start": offset,
            "start_char": char_offset,
            "end": None,
            "end_char": 0,
            "count": 0,
            "total": total,
            "next_offset": None,
            "next_char_offset": 0,
            "truncated": False,
            "truncated_by_chars": False,
            "truncated_by_lines": False,
        }

    first_line = lines[start_index]
    if char_offset > len(first_line):
        raise ValueError(
            f"char_offset={char_offset} 超过第 {offset} 行长度 "
            f"{len(first_line)}"
        )

    pieces: list[str] = []
    used_chars = 0
    line_index = start_index
    current_char = char_offset
    touched_lines = 0

    end_line: int | None = None
    end_char = 0
    next_offset: int | None = None
    next_char_offset = 0
    truncated_by_chars = False

    while (
        line_index < total
        and touched_lines < effective_limit
    ):
        line_number = line_index + 1
        line = lines[line_index]

        separator = "\n" if pieces else ""

        if current_char == 0:
            prefix = f"{line_number}: "
        else:
            prefix = f"{line_number}[{current_char}:]: "

        available = (
            max_chars
            - used_chars
            - len(separator)
            - len(prefix)
        )

        if available <= 0:
            next_offset = line_number
            next_char_offset = current_char
            truncated_by_chars = True
            break

        remaining = line[current_char:]
        fragment = remaining[:available]

        rendered = separator + prefix + fragment
        pieces.append(rendered)
        used_chars += len(rendered)
        touched_lines += 1

        end_line = line_number
        end_char = current_char + len(fragment)

        if end_char < len(line):
            # 本次在当前行中间结束。
            next_offset = line_number
            next_char_offset = end_char
            truncated_by_chars = True
            break

        # 当前行已经完整读取。
        line_index += 1
        current_char = 0

    truncated_by_lines = (
        not truncated_by_chars
        and line_index < total
        and touched_lines >= effective_limit
    )

    if truncated_by_lines:
        next_offset = line_index + 1
        next_char_offset = 0

    if (
        not truncated_by_chars
        and not truncated_by_lines
        and line_index < total
    ):
        next_offset = line_index + 1
        next_char_offset = 0

    has_more = next_offset is not None

    return {
        "content": "".join(pieces),
        "start": offset,
        "start_char": char_offset,
        "end": end_line,
        "end_char": end_char,
        "count": touched_lines,
        "total": total,
        "next_offset": next_offset,
        "next_char_offset": next_char_offset,
        "truncated": has_more,
        "truncated_by_chars": truncated_by_chars,
        "truncated_by_lines": truncated_by_lines,
    }

def read_docx_file(
    path: Path,
    offset: int,
    char_offset: int,
    limit: int | None,
    max_chars: int,
) -> dict:
    doc = Document(path)

    paragraphs = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text:
            paragraphs.append(text)

    window = read_numbered_lines(
        paragraphs,
        offset=offset,
        char_offset=char_offset,
        limit=limit,
        max_chars=max_chars
    )

    return OutputSchema(
        ok=True,
        error="",
        file_path=str(path),
        file_type=".docx",
        **window,
    ).model_dump()

def read_text_file(
    path: Path,
    offset: int,
    char_offset: int,
    limit: int | None,
    max_chars: int,
) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    window = read_numbered_lines(
        lines,
        offset=offset,
        char_offset=char_offset,
        limit=limit,
        max_chars=max_chars,
    )

    return OutputSchema(
        ok=True,
        error="",
        file_path=str(path),
        file_type=path.suffix.lower(),
        **window,
    ).model_dump()

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"读取文件失败，错误信息：{output.error}"

    position = (
        f"本次从第 {output.start} 行第 {output.start_char} 个字符开始，"
        f"读取到第 {output.end} 行第 {output.end_char} 个字符；"
        f"共触及 {output.count} 行，文件总行数为 {output.total}。"
    )

    if not output.truncated:
        continuation = "文件内容已经读取完毕。"
    else:
        reasons = []
        if output.truncated_by_chars:
            reasons.append("达到字符上限")
        if output.truncated_by_lines:
            reasons.append("达到行数上限")

        continuation = (
            f"内容尚未读取完毕，原因：{'、'.join(reasons)}。"
            "继续读取时必须使用："
            f"offset={output.next_offset}, "
            f"char_offset={output.next_char_offset}。"
        )

    return (
        f"已读取内容如下：\n{output.content}\n"
        f"{position}\n"
        f"{continuation}"
    )