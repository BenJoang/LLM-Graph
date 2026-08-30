from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from src.tools.ShellTool import runner
from src.tools.ShellTool.tool import DEFAULT_TIMEOUT, InputSchema


WINDOWS_POWERSHELL = shutil.which("powershell") if os.name == "nt" else None


class OutputCollectorTests(unittest.TestCase):
    def test_keeps_tail_and_spills_full_output(self) -> None:
        collector = runner.OutputCollector(
            "stdout",
            max_output_bytes=5,
            max_spill_bytes=100,
        )

        collector.append(b"abc")
        collector.append(b"defgh")
        result = collector.result()

        self.assertTrue(result.truncated)
        self.assertEqual(result.text, "defgh")
        self.assertEqual(result.total_bytes, 8)
        self.assertIsNotNone(result.spill_path)
        spill_path = Path(result.spill_path or "")
        try:
            self.assertEqual(spill_path.read_bytes(), b"abcdefgh")
        finally:
            spill_path.unlink(missing_ok=True)


class ShellConfigurationTests(unittest.TestCase):
    def test_default_timeout_is_two_minutes(self) -> None:
        self.assertEqual(DEFAULT_TIMEOUT, 120)
        self.assertEqual(InputSchema(command="Write-Output ok").timeout, 120)

    def test_powershell_command_captures_terminal_status_without_stale_exit(self) -> None:
        invocation = runner.ShellInvocation(
            name="powershell",
            executable="powershell.exe",
            kind="powershell",
        )

        command = invocation.argv("cmd.exe /d /c exit 7; Write-Output recovered")[-1]

        self.assertNotIn("& {", command)
        self.assertIn("\ncmd.exe /d /c exit 7; Write-Output recovered\n", command)
        self.assertIn("$__llmGraphShellSuccess = $?", command)
        self.assertIn("-and $__llmGraphShellExitCode -ne 0", command)

    def test_prompt_forbids_unsupported_chain_operators(self) -> None:
        prompt_path = Path(runner.__file__).with_name("prompt.md")
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertIn("不要使用 `&&` 或 `||`", prompt)
        self.assertIn("cmd1; if ($?) { cmd2 }", prompt)
        self.assertNotIn("PowerShell 5.1", prompt)


@unittest.skipUnless(WINDOWS_POWERSHELL, "requires Windows PowerShell")
class WindowsPowerShellIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.invocation = runner.ShellInvocation(
            name="powershell",
            executable=str(WINDOWS_POWERSHELL),
            kind="powershell",
        )

    def run_shell(self, command: str, timeout: int = 5) -> runner.ShellRunResult:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "resolve_shell", return_value=self.invocation):
                return asyncio.run(
                    runner.run_shell(
                        command=command,
                        cwd=Path(directory),
                        timeout_seconds=timeout,
                    )
                )

    def test_utf8_output(self) -> None:
        result = self.run_shell("Write-Output '中文输出'")

        self.assertEqual(result.exit_code, 0)
        self.assertIn("中文输出", result.stdout.text)

    def test_unknown_command_returns_nonzero(self) -> None:
        result = self.run_shell("not_a_real_command")

        self.assertEqual(result.status, "completed")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not_a_real_command", result.stderr.text)

    def test_failed_cmdlet_returns_nonzero(self) -> None:
        result = self.run_shell(
            "Get-Item -LiteralPath 'Z:\\__llm_graph_missing__'"
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Get-Item", result.stderr.text)

    def test_unsupported_chain_operator_points_to_user_command(self) -> None:
        result = self.run_shell("Write-Output a && Write-Output b")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("At line:4 char:", result.stderr.text)
        self.assertIn("&&", result.stderr.text)

    def test_native_exit_code_and_final_command_semantics(self) -> None:
        failed = self.run_shell("cmd.exe /d /c exit 7")
        recovered = self.run_shell(
            "cmd.exe /d /c exit 7; Write-Output recovered"
        )

        self.assertEqual(failed.exit_code, 7)
        self.assertEqual(recovered.exit_code, 0)
        self.assertIn("recovered", recovered.stdout.text)

    def test_timeout_returns_structured_result(self) -> None:
        started = time.monotonic()
        result = self.run_shell("Start-Sleep -Seconds 5", timeout=1)

        self.assertTrue(result.timed_out)
        self.assertEqual(result.status, "timed_out")
        self.assertLess(time.monotonic() - started, 5)

    def test_runs_inside_selector_event_loop(self) -> None:
        loop = asyncio.SelectorEventLoop()
        try:
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(
                    runner,
                    "resolve_shell",
                    return_value=self.invocation,
                ):
                    result = loop.run_until_complete(
                        runner.run_shell(
                            command="Write-Output selector-ok",
                            cwd=Path(directory),
                            timeout_seconds=5,
                        )
                    )
        finally:
            loop.close()

        self.assertEqual(result.exit_code, 0)
        self.assertIn("selector-ok", result.stdout.text)

    def test_cancellation_stops_process_promptly(self) -> None:
        async def cancel_run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(
                    runner,
                    "resolve_shell",
                    return_value=self.invocation,
                ):
                    task = asyncio.create_task(
                        runner.run_shell(
                            command="Start-Sleep -Seconds 5",
                            cwd=Path(directory),
                            timeout_seconds=10,
                        )
                    )
                    await asyncio.sleep(0.2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        started = time.monotonic()
        asyncio.run(cancel_run())
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
