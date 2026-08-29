from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .runner import (
    CollectedOutput,
    ShellRunResult,
    resolve_workdir,
    run_shell,
)


TOOL_NAME = "shell_tool"
TOOL_DIR = Path(__file__).resolve().parent

IS_READ_ONLY = False
IS_DESTRUCTIVE = True

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 600


class InputSchema(BaseModel):
    command: str = Field(
        min_length=1,
        description="要执行的 shell 命令",
    )
    cwd: str | None = Field(
        default=None,
        description=(
            "命令的工作目录；相对路径基于 agent 工作目录解析，"
            "不填时使用 agent 工作目录"
        ),
    )
    timeout: int = Field(
        default=DEFAULT_TIMEOUT,
        ge=1,
        le=MAX_TIMEOUT,
        description=(
            f"命令超时时间（秒），默认 {DEFAULT_TIMEOUT}，"
            f"最大 {MAX_TIMEOUT}"
        ),
    )


class StreamResult(BaseModel):
    text: str = ""
    truncated: bool = False
    total_bytes: int = 0
    spill_path: str | None = None


class ShellResult(BaseModel):
    status: Literal["completed", "timed_out", "spawn_failed"]
    command: str
    cwd: str
    shell: str
    exit_code: int | None = None
    timed_out: bool = False
    timeout_seconds: int
    duration_ms: int
    stdout: StreamResult = Field(default_factory=StreamResult)
    stderr: StreamResult = Field(default_factory=StreamResult)


class OutputSchema(BaseModel):
    ok: bool = Field(description="shell 工具基础设施是否成功完成调用")
    error: str = Field(default="", description="工具级错误；非零退出码不属于工具级错误")
    data: ShellResult | None = None


def get_input_schema() -> dict:
    return InputSchema.model_json_schema()


def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()


def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        input_data = InputSchema(**kwargs)
    except ValidationError as error:
        return False, str(error)

    if not input_data.command.strip():
        return False, "command 不能为空或只包含空白字符"

    return True, ""


def check_permissions(**kwargs) -> tuple[bool, str]:
    # 当前项目尚未提供逐次审批接口。先保持与现有工具一致的钩子，
    # ShellTool 始终标记为 destructive，后续权限层可在这里接入。
    return True, ""


def prepare_input(**kwargs) -> tuple[InputSchema | None, dict | None]:
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
    try:
        input_data = InputSchema(**kwargs)
    except ValidationError:
        return f"{TOOL_NAME} input invalid"

    return f"Run {TOOL_NAME}: {input_data.command}"


def stream_result(output: CollectedOutput) -> StreamResult:
    return StreamResult(
        text=output.text,
        truncated=output.truncated,
        total_bytes=output.total_bytes,
        spill_path=output.spill_path,
    )


def shell_result(result: ShellRunResult) -> ShellResult:
    return ShellResult(
        status=result.status,
        command=result.command,
        cwd=result.cwd,
        shell=result.shell,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        timeout_seconds=result.timeout_seconds,
        duration_ms=result.duration_ms,
        stdout=stream_result(result.stdout),
        stderr=stream_result(result.stderr),
    )


async def acall(
    _working_dir: str | None = None,
    **kwargs,
) -> dict:
    input_data, failure = prepare_input(**kwargs)
    if failure is not None:
        return failure

    try:
        cwd = resolve_workdir(
            input_data.cwd,
            _working_dir,
        )
        result = await run_shell(
            command=input_data.command,
            cwd=cwd,
            timeout_seconds=input_data.timeout,
        )
        data = shell_result(result)
        return OutputSchema(
            ok=not result.timed_out,
            error=(
                ""
                if not result.timed_out
                else f"命令执行超时：超过 {input_data.timeout} 秒"
            ),
            data=data,
        ).model_dump()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        fallback_cwd = input_data.cwd or _working_dir or str(Path.cwd())
        return OutputSchema(
            ok=False,
            error=str(error),
            data=ShellResult(
                status="spawn_failed",
                command=input_data.command,
                cwd=str(fallback_cwd),
                shell="",
                timeout_seconds=input_data.timeout,
                duration_ms=0,
            ),
        ).model_dump()


def call(
    _working_dir: str | None = None,
    **kwargs,
) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            acall(
                _working_dir=_working_dir,
                **kwargs,
            )
        )

    raise RuntimeError(
        f"{TOOL_NAME}.call() 不能在正在运行的事件循环中调用；"
        "请使用 await acall(...)"
    )


def render_stream(
    label: str,
    stream: StreamResult,
) -> list[str]:
    if not stream.text and not stream.truncated:
        return []

    lines = ["", f"{label}:"]
    if stream.text:
        lines.append(stream.text)
    if stream.truncated:
        location = stream.spill_path or "完整输出不可用"
        lines.append(
            f"[{label} 已截断；总计 {stream.total_bytes} bytes；"
            f"完整输出：{location}]"
        )
    return lines


def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)
    data = output.data
    if data is None:
        return f"shell_tool 执行失败：{output.error}"

    lines = [
        f"执行命令：{data.command}",
        f"工作目录：{data.cwd}",
    ]
    if data.shell:
        lines.append(f"Shell：{data.shell}")
    lines.append(f"耗时：{data.duration_ms} ms")

    if data.status == "spawn_failed":
        lines.append(f"状态：启动失败，{output.error}")
    elif data.timed_out:
        lines.append(
            f"状态：执行超时（{data.timeout_seconds} 秒）；"
            "命令可能已经产生部分副作用，重试前请先检查状态"
        )
    else:
        lines.append(f"退出码：{data.exit_code}")
        if data.exit_code == 0:
            lines.append("状态：执行完成")
        else:
            lines.append("状态：命令以非零退出码完成")

    lines.extend(render_stream("stdout", data.stdout))
    lines.extend(render_stream("stderr", data.stderr))

    if (
        data.status == "completed"
        and not data.stdout.text
        and not data.stderr.text
    ):
        lines.extend(["", "(no output)"])

    return "\n".join(lines)
