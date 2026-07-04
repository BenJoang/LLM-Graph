from pathlib import Path
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

TOOL_NAME = "python_tool"
TOOL_DIR = Path(__file__).resolve().parent

IS_READ_ONLY = False
IS_DESTRUCTIVE = True
MAX_RESULT_CHARS = 10000

DEFAULT_TIME_OUT = 30
MAX_TIMEOUT = 120

class InputSchema(BaseModel):
    mode: Literal["script", "inline"] = "script"

    script_path: str | None = Field(default=None, description="mode=script 时要执行的 .py 脚本文件绝对路径")
    code: str | None = Field(default=None, description="mode=inline 时通过 python -c 执行的代码")

    cwd: str | None = Field(default=None, description="执行脚本时的工作目录，默认脚本所在目录")
    args: list[str] = Field(default_factory=list,description="传给脚本或 -c 代码的 argv 参数",)

    python_path: str | None = Field(
        default=None,
        description="指定用于执行脚本的 Python 解释器路径，例如 .venv/Scripts/python.exe；不填则使用当前系统默认 Python",
    )
    timeout: int = Field(default=DEFAULT_TIME_OUT, description=f"超时时间，默认{DEFAULT_TIME_OUT}，最大{MAX_TIMEOUT}")

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
        resolve_python_path(input_data.python_path)

        if input_data.mode == "script":
            if not input_data.script_path:
                return False, "mode=script 时必须提供 script_path"
            if input_data.code:
                return False, "mode=script 时不能提供 code"
            script_path = resolve_script_path(input_data.script_path)
            resolve_cwd(input_data.cwd, script_path)
            
        if input_data.mode == "inline":
            if not input_data.code:
                return False, "mode=inline 时必须提供 code"
            if input_data.script_path:
                return False, "mode=inline 时不能提供 script_path"
            resolve_inline_cwd(input_data.cwd)
        
        

    except ValidationError as e:
        return False, str(e)
    
    except Exception as e:
        return False, str(e)
    
    
    
    
    if input_data.timeout <= 0:
        return False, "timeout 必须大于0"
    
    if input_data.timeout > MAX_TIMEOUT:
        return False, f"timeout 不能超过 {MAX_TIMEOUT} 秒"

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

def normalize_process_output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

def resolve_python_path(python_path: str | None) -> Path:
    if python_path is None:
        return Path(sys.executable).resolve()

    path = resolve_existing_path(python_path, must_be_file=True)

    name = path.name.lower()
    if name not in {"python.exe", "python", "python3"}:
        raise ValueError(f"python_path 看起来不是 Python 解释器：{path}")

    return path

def resolve_cwd(cwd: str | None, script_path: Path) -> Path:
    if cwd is None:
        return script_path.parent.resolve()

    return resolve_existing_path(cwd, must_be_file=False)

def resolve_inline_cwd(cwd: str | None) -> Path:
    if cwd is None:
        return Path.cwd().resolve()
    return resolve_existing_path(cwd, must_be_file=False)

def resolve_existing_path(path_text: str, *, must_be_file: bool | None = None) -> Path:
    path = Path(path_text).expanduser().resolve()

    if not path.exists():
        raise ValueError(f"该路径不存在：{path}")

    if must_be_file is True and not path.is_file():
        raise ValueError(f"该路径没有指向一个文件：{path}")

    if must_be_file is False and not path.is_dir():
        raise ValueError(f"该路径没有指向一个目录：{path}")

    #if not is_inside_allowed_base(path):
    #    raise ValueError(f"路径不在允许的项目目录内：{path}")

    return path

def resolve_script_path(script_path: str) -> Path:
    path = resolve_existing_path(script_path, must_be_file=True)

    if path.suffix.lower() != ".py":
        raise ValueError(f"只允许执行 .py 文件：{path}")

    return path 


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
        python_path = resolve_python_path(input_data.python_path)

        if input_data.mode == "script":
            script_path = resolve_script_path(input_data.script_path)
            cwd = resolve_cwd(input_data.cwd, script_path)

            command = [
                str(python_path),
                str(script_path),
                *input_data.args,
            ]
        if input_data.mode == "inline":
            cwd = resolve_inline_cwd(input_data.cwd)
            
            command = [
                str(python_path),
                "-c",
                input_data.code,
                *input_data.args,
            ]

        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                timeout=input_data.timeout,
                capture_output=True,
                text=True,
                shell=False,
            )

            stdout = completed.stdout
            stderr = completed.stderr
            
            result_data = {
                "returncode": completed.returncode,
                "command": command,
                "cwd": str(cwd),
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False
            }

            return OutputSchema(
                ok=completed.returncode == 0,
                error="" if completed.returncode == 0 else f"脚本退出码非 0：{completed.returncode}",
                data=result_data,
            ).model_dump()
        except subprocess.TimeoutExpired as e:
            result_data = {
                "returncode": None,
                "command": command,
                "cwd": str(cwd),
                "stdout": normalize_process_output(e.stdout),
                "stderr": normalize_process_output(e.stderr),
                "timed_out": True,
                "timeout": input_data.timeout,
            }

            return OutputSchema(
                ok=False,
                error=f"脚本执行超时：超过 {input_data.timeout} 秒",
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
    data = output.data if isinstance(output.data, dict) else {}

    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")
    command = data.get("command", [])
    cwd = data.get("cwd", "")
    returncode = data.get("returncode")
    timed_out = data.get("timed_out", False)

    lines = [
        f"执行命令：{' '.join(command)}" if command else "执行命令：",
    ]

    if cwd:
        lines.append(f"工作目录：{cwd}")

    if returncode is not None:
        lines.append(f"退出码：{returncode}")

    if timed_out:
        lines.append("状态：执行超时")
    elif output.ok:
        lines.append("状态：执行成功")
    else:
        lines.append(f"状态：执行失败，{output.error}")

    if stdout:
        lines.extend(["", "stdout:", stdout])

    if stderr:
        lines.extend(["", "stderr:", stderr])

    if not stdout and not stderr:
        lines.append("")
        lines.append("脚本没有输出。")

    return "\n".join(lines)
