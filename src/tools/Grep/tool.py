from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
import json
import shutil
import subprocess
import asyncio

TOOL_NAME = "grep"
TOOL_DIR = Path(__file__).resolve().parent

IS_READ_ONLY = True
IS_DESTRUCTIVE = False
MAX_RESULT_CHARS = 10000
DEFAULT_TIMEOUT = 20
MAX_LINE_CHARS = 500

class InputSchema(BaseModel):
    pattern: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="要搜索的普通文本或正则表达式，搜索内容始终放在此字段",
    )
    path: str = Field(
        description=(
            "要搜索的文件或目录的绝对路径。"
            "搜索当前项目时，使用 system prompt 中 "
            "<working-directory> 提供的路径；"
            "也可以传入其他文件或目录的绝对路径。"
        ),
    )
    include: str | None = Field(
        default=None,
        description='文件过滤规则，例如 "*.py" 或 "*.{py,ts}"',
    )
    match_mode: Literal["literal", "regex"] = Field(
        default="literal",
        description="literal 表示普通文本匹配，regex 表示正则表达式匹配",
    )
    ignore_case: bool = Field(
        default=False,
        description="是否忽略大小写",
    )
    context: int = Field(
        default=0,
        ge=0,
        le=5,
        description="返回匹配行前后多少行",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="最多返回多少个匹配位置",
    )

class MatchItem(BaseModel):
    file_path: str
    line_number: int
    start_line: int
    end_line: int
    snippet: str


class OutputSchema(BaseModel):
    ok: bool
    error: str = ""
    root: str = ""
    keyword: str = ""
    matches: list[MatchItem] = Field(default_factory=list)
    count: int = 0
    truncated: bool = False


def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()

def run_ripgrep(
    command: list[str],
    cwd: Path,
    limit: int,
) -> tuple[list[MatchItem], bool]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DEFAULT_TIMEOUT,
        shell=False,
    )

    if completed.returncode not in {0, 1}:
        error = completed.stderr.strip()
        raise RuntimeError(error or "ripgrep 执行失败")

    matches: list[MatchItem] = []
    truncated = False

    for raw_line in completed.stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "match":
            continue

        data = event.get("data", {})

        path_text = data.get("path", {}).get("text")
        line_number = data.get("line_number")
        line_text = data.get("lines", {}).get("text", "")

        if not path_text or not isinstance(line_number, int):
            continue

        if len(matches) >= limit:
            truncated = True
            break

        file_path = Path(path_text)

        if not file_path.is_absolute():
            file_path = (cwd / file_path).resolve()

        content = line_text.rstrip("\r\n")

        if len(content) > MAX_LINE_CHARS:
            content = (
                content[:MAX_LINE_CHARS]
                + "... [truncated]"
            )

        matches.append(
            MatchItem(
                file_path=str(file_path),
                line_number=line_number,
                start_line=line_number,
                end_line=line_number,
                snippet=f"{line_number}: {content}",
            )
        )

    return matches, truncated

def build_rg_command(
    input_data: InputSchema,
    target: Path,
) -> list[str]:
    rg_path = shutil.which("rg")

    if rg_path is None:
        raise RuntimeError(
            "没有找到 ripgrep，请安装 rg 并确保它位于 PATH 中"
        )

    command = [
        rg_path,
        "--no-config",
        "--json",
        "--line-number",
        "--color=never",
        "--no-messages",
    ]

    if input_data.match_mode == "literal":
        command.append("--fixed-strings")

    if input_data.ignore_case:
        command.append("--ignore-case")

    if input_data.include:
        command.extend([
            "--glob",
            input_data.include,
        ])

    command.extend([
        "--",
        input_data.pattern,
        str(target),
    ])

    return command
    
def validate_input(**kwargs) -> tuple[bool, str]:

    try:
        input_data = InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)

    return True, ""

def resolve_search_path(search_path: str) -> Path:
    candidate = Path(search_path).expanduser()

    if not candidate.is_absolute():
        raise ValueError(
            "path 必须是绝对路径。"
            "搜索当前项目时，请使用 system prompt 中 "
            "<working-directory> 的值。"
        )

    target = candidate.resolve()

    if not target.exists():
        raise ValueError(f"搜索路径不存在：{target}")

    if not target.is_file() and not target.is_dir():
        raise ValueError(f"搜索路径不是文件或目录：{target}")

    return target

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

def truncate_line(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line

    return line[:MAX_LINE_CHARS] + "... [truncated]"


def add_match_context(
    matches: list[MatchItem],
    context: int,
) -> list[MatchItem]:
    if context == 0:
        return matches

    file_cache: dict[Path, list[str]] = {}
    results: list[MatchItem] = []

    for match in matches:
        file_path = Path(match.file_path)

        try:
            if file_path not in file_cache:
                text = file_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                file_cache[file_path] = text.splitlines()

            file_lines = file_cache[file_path]

            start_line = max(
                1,
                match.line_number - context,
            )
            end_line = min(
                len(file_lines),
                match.line_number + context,
            )

            snippet_lines = []

            for line_number in range(
                start_line,
                end_line + 1,
            ):
                content = truncate_line(
                    file_lines[line_number - 1]
                )

                marker = ">" if line_number == match.line_number else " "

                snippet_lines.append(
                    f"{marker} {line_number}: {content}"
                )

            results.append(
                match.model_copy(
                    update={
                        "start_line": start_line,
                        "end_line": end_line,
                        "snippet": "\n".join(snippet_lines),
                    }
                )
            )

        except Exception:
            # 读取上下文失败时，仍保留 ripgrep 返回的匹配行
            results.append(match)

    return results

def limit_matches_by_chars(
    matches: list[MatchItem],
    max_chars: int = MAX_RESULT_CHARS,
) -> tuple[list[MatchItem], bool]:
    kept: list[MatchItem] = []
    used_chars = 0
    current_file = ""

    for match in matches:
        additional_chars = len(match.snippet) + 1

        if match.file_path != current_file:
            additional_chars += len(match.file_path) + 2

        if used_chars + additional_chars > max_chars:
            return kept, True

        kept.append(match)
        used_chars += additional_chars
        current_file = match.file_path

    return kept, False


def call(**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)

    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)

        target = resolve_search_path(input_data.path)

        command = build_rg_command(
            input_data=input_data,
            target=target,
        )

        cwd = target if target.is_dir() else target.parent

        matches, truncated = run_ripgrep(
            command=command,
            cwd=cwd,
            limit=input_data.limit,
        )
        matches = add_match_context(
            matches,
            input_data.context,
        )
        matches, truncated_by_chars = limit_matches_by_chars(
            matches,
            max_chars=MAX_RESULT_CHARS - 500,
        )

        truncated = truncated or truncated_by_chars

        return OutputSchema(
            ok=True,
            root=str(target),
            keyword=input_data.pattern,
            matches=matches,
            count=len(matches),
            truncated=truncated,
        ).model_dump()

    except subprocess.TimeoutExpired:
        return OutputSchema(
            ok=False,
            error=f"搜索超时，超过 {DEFAULT_TIMEOUT} 秒",
        ).model_dump()

    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
        ).model_dump()

async def acall(**kwargs) -> dict:
    return await asyncio.to_thread(
        call,
        **kwargs,
    )

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"grep工具执行失败：{output.error}"

    if not output.matches:
        return (
            f"没有在 {output.root} 中找到关键词 "
            f"'{output.keyword}'。"
        )

    lines = [
        f"在 {output.root} 中找到 {output.count} 个匹配："
    ]

    current_file = ""

    for match in output.matches:
        if current_file != match.file_path:
            current_file = match.file_path
            lines.extend(["", current_file])

        lines.append(match.snippet)

    if output.truncated:
        lines.extend([
            "",
            "结果已截断，请使用更具体的关键词、path 或 include。",
        ])

    return "\n".join(lines)
