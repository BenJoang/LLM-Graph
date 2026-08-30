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
    "[System.Text.UTF8Encoding]::new($false)\n"
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
    "if (Test-Path variable:PSStyle) {\n"
    "    $PSStyle.OutputRendering = 'PlainText'\n"
    "}\n"
)

POWERSHELL_EXIT_EPILOGUE = (
    "$__llmGraphShellSuccess = $?\n"
    "$__llmGraphShellExitCode = $global:LASTEXITCODE\n"
    "if (-not $__llmGraphShellSuccess) {\n"
    "    if ($null -ne $__llmGraphShellExitCode "
    "-and $__llmGraphShellExitCode -ne 0) {\n"
    "        exit $__llmGraphShellExitCode\n"
    "    }\n"
    "    exit 1\n"
    "}\n"
    "exit 0"
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
                "$global:LASTEXITCODE = $null\n"
                f"{command}\n"
                f"{POWERSHELL_EXIT_EPILOGUE}"
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


def collect_stream(
    reader,
    collector: OutputCollector,
) -> None:
    try:
        while True:
            chunk = reader.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            collector.append(chunk)
    except (OSError, ValueError):
        # The supervising thread may close a pipe to unblock a reader after
        # the process tree has been terminated.
        pass
    finally:
        try:
            reader.close()
        except OSError:
            pass
        collector.close()


def terminate_process_tree(
    process: subprocess.Popen,
    grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS,
) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=grace_seconds,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass


def finish_collectors(
    threads: list[threading.Thread],
    streams: list,
    grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS,
) -> None:
    for thread in threads:
        thread.join(timeout=grace_seconds)

    if all(not thread.is_alive() for thread in threads):
        return

    for stream in streams:
        try:
            stream.close()
        except OSError:
            pass

    for thread in threads:
        if thread.is_alive():
            thread.join(timeout=grace_seconds)


class _ShellRunCancelled(Exception):
    pass


def _run_shell_sync(
    *,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int,
    max_spill_bytes: int,
    cancel_requested: threading.Event,
) -> ShellRunResult:
    shell = resolve_shell()
    creation_options: dict[str, object] = {}
    if os.name == "nt":
        creation_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation_options["start_new_session"] = True

    started = time.monotonic()
    process = subprocess.Popen(
        shell.argv(command),
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        **creation_options,
    )

    if process.stdout is None or process.stderr is None:
        terminate_process_tree(process)
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
    streams = [process.stdout, process.stderr]
    collector_threads = [
        threading.Thread(
            target=collect_stream,
            args=(process.stdout, stdout_collector),
            name="llm-graph-shell-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=collect_stream,
            args=(process.stderr, stderr_collector),
            name="llm-graph-shell-stderr",
            daemon=True,
        ),
    ]
    for thread in collector_threads:
        thread.start()

    timed_out = False
    cancelled = False
    deadline = started + timeout_seconds
    try:
        while process.poll() is None:
            if cancel_requested.is_set():
                cancelled = True
                terminate_process_tree(process)
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                terminate_process_tree(process)
                break

            cancel_requested.wait(timeout=min(0.05, remaining))
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
        finish_collectors(collector_threads, streams)

    if cancelled:
        raise _ShellRunCancelled()

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


async def run_shell(
    *,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_spill_bytes: int = DEFAULT_MAX_SPILL_BYTES,
) -> ShellRunResult:
    cancel_requested = threading.Event()
    worker = asyncio.create_task(
        asyncio.to_thread(
            _run_shell_sync,
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_spill_bytes=max_spill_bytes,
            cancel_requested=cancel_requested,
        )
    )

    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_requested.set()
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        raise
