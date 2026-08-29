from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Literal
from uuid import uuid4


DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024
DEFAULT_MAX_SPILL_BYTES = 64 * 1024 * 1024
DEFAULT_KILL_GRACE_SECONDS = 3.0
READ_CHUNK_BYTES = 16 * 1024

POWERSHELL_ENCODING_PREAMBLE = (
    "[Console]::OutputEncoding = "
    "[System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)


@dataclass(frozen=True)
class ShellInvocation:
    name: str
    executable: str
    kind: Literal["powershell", "posix"]

    def argv(self, command: str) -> list[str]:
        if self.kind == "powershell":
            wrapped_command = (
                f"{POWERSHELL_ENCODING_PREAMBLE}"
                "$global:LASTEXITCODE = $null; "
                f"& {{ {command} }}; "
                "$__llmGraphShellSuccess = $?; "
                "$__llmGraphShellExitCode = $LASTEXITCODE; "
                "if ($null -ne $__llmGraphShellExitCode) "
                "{ exit $__llmGraphShellExitCode }; "
                "if (-not $__llmGraphShellSuccess) { exit 1 }"
            )
            return [
                self.executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                wrapped_command,
            ]

        return [self.executable, "-c", command]


@dataclass(frozen=True)
class CollectedOutput:
    text: str
    truncated: bool
    total_bytes: int
    spill_path: str | None = None


@dataclass(frozen=True)
class ShellRunResult:
    status: Literal["completed", "timed_out"]
    command: str
    cwd: str
    shell: str
    exit_code: int | None
    timed_out: bool
    timeout_seconds: int
    duration_ms: int
    stdout: CollectedOutput
    stderr: CollectedOutput


class OutputCollector:
    def __init__(
        self,
        stream_name: str,
        *,
        max_output_bytes: int,
        max_spill_bytes: int,
    ) -> None:
        self.stream_name = stream_name
        self.max_output_bytes = max_output_bytes
        self.max_spill_bytes = max_spill_bytes
        self.tail = bytearray()
        self.total_bytes = 0
        self.truncated = False
        self._spill_path: Path | None = None
        self._spill_file = None
        self._spill_bytes = 0
        self._spill_complete = True

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return

        previous = bytes(self.tail) if not self.truncated else b""
        self.total_bytes += len(chunk)

        if not self.truncated and self.total_bytes > self.max_output_bytes:
            self.truncated = True
            self._start_spill(previous)

        if self.truncated:
            self._write_spill(chunk)

        self.tail.extend(chunk)
        overflow = len(self.tail) - self.max_output_bytes
        if overflow > 0:
            del self.tail[:overflow]

    def _start_spill(self, initial: bytes) -> None:
        try:
            directory = Path(tempfile.gettempdir()) / "llm_graph_shell_outputs"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{uuid4().hex}-{self.stream_name}.log"
            self._spill_file = path.open("xb")
            self._spill_path = path
            if initial:
                self._spill_file.write(initial)
                self._spill_bytes = len(initial)
        except OSError:
            self._discard_spill()

    def _write_spill(self, chunk: bytes) -> None:
        if self._spill_file is None:
            return

        if self._spill_bytes + len(chunk) > self.max_spill_bytes:
            self._discard_spill()
            return

        try:
            self._spill_file.write(chunk)
            self._spill_bytes += len(chunk)
        except OSError:
            self._discard_spill()

    def _discard_spill(self) -> None:
        self._spill_complete = False
        spill_file = self._spill_file
        spill_path = self._spill_path
        self._spill_file = None
        self._spill_path = None

        if spill_file is not None:
            try:
                spill_file.close()
            except OSError:
                pass

        if spill_path is not None:
            try:
                spill_path.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self) -> None:
        if self._spill_file is None:
            return

        try:
            self._spill_file.close()
        except OSError:
            self._discard_spill()
        finally:
            self._spill_file = None

    def result(self) -> CollectedOutput:
        self.close()
        spill_path = (
            str(self._spill_path)
            if self._spill_complete and self._spill_path is not None
            else None
        )
        return CollectedOutput(
            text=bytes(self.tail).decode("utf-8", errors="replace"),
            truncated=self.truncated,
            total_bytes=self.total_bytes,
            spill_path=spill_path,
        )


def resolve_shell() -> ShellInvocation:
    if os.name == "nt":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise RuntimeError("没有找到 pwsh 或 powershell")
        return ShellInvocation(
            name=Path(executable).stem,
            executable=executable,
            kind="powershell",
        )

    executable = shutil.which("bash") or shutil.which("sh")
    if executable is None:
        raise RuntimeError("没有找到 bash 或 sh")
    return ShellInvocation(
        name=Path(executable).name,
        executable=executable,
        kind="posix",
    )


def resolve_workdir(
    requested: str | None,
    default: str | None,
) -> Path:
    base = Path(default).expanduser() if default else Path.cwd()
    candidate = Path(requested).expanduser() if requested else base
    if not candidate.is_absolute():
        candidate = base / candidate

    resolved = candidate.resolve()
    if not resolved.exists():
        raise ValueError(f"工作目录不存在：{resolved}")
    if not resolved.is_dir():
        raise ValueError(f"工作目录不是文件夹：{resolved}")
    return resolved


async def collect_stream(
    reader: asyncio.StreamReader,
    collector: OutputCollector,
) -> None:
    try:
        while True:
            chunk = await reader.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            collector.append(chunk)
    finally:
        collector.close()


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS,
) -> None:
    if process.returncode is not None:
        return

    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await killer.wait()
        except (FileNotFoundError, OSError):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
            return
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except TimeoutError:
        pass


async def finish_collectors(
    tasks: list[asyncio.Task[None]],
    grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS,
) -> None:
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=grace_seconds,
        )
    except TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _requires_threaded_windows_subprocess() -> bool:
    """Return whether the active Windows loop cannot create subprocesses.

    Windows' SelectorEventLoop intentionally does not implement asyncio's
    subprocess transport. The API uses that loop for async Psycopg support,
    so shell processes must be supervised outside the active event loop.
    """

    if os.name != "nt":
        return False

    proactor_loop = getattr(asyncio, "ProactorEventLoop", None)
    return proactor_loop is None or not isinstance(
        asyncio.get_running_loop(),
        proactor_loop,
    )


class _ProactorThreadState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[ShellRunResult] | None = None
        self._cancel_requested = False

    def attach(
        self,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task[ShellRunResult],
    ) -> None:
        with self._lock:
            self._loop = loop
            self._task = task
            if self._cancel_requested:
                loop.call_soon(task.cancel)

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            if self._loop is not None and self._task is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)

    def detach(self) -> None:
        with self._lock:
            self._loop = None
            self._task = None


def _run_shell_in_proactor_thread(
    *,
    state: _ProactorThreadState,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int,
    max_spill_bytes: int,
) -> ShellRunResult:
    proactor_loop = getattr(asyncio, "ProactorEventLoop", None)
    if proactor_loop is None:
        raise RuntimeError("当前 Python 不提供 Windows ProactorEventLoop")

    loop = proactor_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(
        run_shell(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_spill_bytes=max_spill_bytes,
        )
    )
    state.attach(loop, task)
    try:
        return loop.run_until_complete(task)
    finally:
        state.detach()
        asyncio.set_event_loop(None)
        loop.close()


async def _run_shell_windows_threaded(
    *,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int,
    max_spill_bytes: int,
) -> ShellRunResult:
    state = _ProactorThreadState()
    worker = asyncio.create_task(
        asyncio.to_thread(
            _run_shell_in_proactor_thread,
            state=state,
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_spill_bytes=max_spill_bytes,
        )
    )

    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        state.request_cancel()
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            pass
        raise


async def run_shell(
    *,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_spill_bytes: int = DEFAULT_MAX_SPILL_BYTES,
) -> ShellRunResult:
    if _requires_threaded_windows_subprocess():
        return await _run_shell_windows_threaded(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_spill_bytes=max_spill_bytes,
        )

    shell = resolve_shell()
    creation_options: dict[str, object] = {}
    if os.name == "nt":
        creation_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation_options["start_new_session"] = True

    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *shell.argv(command),
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **creation_options,
    )

    if process.stdout is None or process.stderr is None:
        await terminate_process_tree(process)
        raise RuntimeError("无法捕获 shell 的 stdout/stderr")

    stdout_collector = OutputCollector(
        "stdout",
        max_output_bytes=max_output_bytes,
        max_spill_bytes=max_spill_bytes,
    )
    stderr_collector = OutputCollector(
        "stderr",
        max_output_bytes=max_output_bytes,
        max_spill_bytes=max_spill_bytes,
    )
    collector_tasks = [
        asyncio.create_task(
            collect_stream(process.stdout, stdout_collector)
        ),
        asyncio.create_task(
            collect_stream(process.stderr, stderr_collector)
        ),
    ]

    timed_out = False
    try:
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            await terminate_process_tree(process)
    except asyncio.CancelledError:
        await asyncio.shield(terminate_process_tree(process))
        await asyncio.shield(finish_collectors(collector_tasks))
        raise
    finally:
        await finish_collectors(collector_tasks)

    duration_ms = round((time.monotonic() - started) * 1000)
    return ShellRunResult(
        status="timed_out" if timed_out else "completed",
        command=command,
        cwd=str(cwd),
        shell=shell.name,
        exit_code=process.returncode,
        timed_out=timed_out,
        timeout_seconds=timeout_seconds,
        duration_ms=duration_ms,
        stdout=stdout_collector.result(),
        stderr=stderr_collector.result(),
    )
