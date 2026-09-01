@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo LLM-Graph setup failed. Review the error above.
    pause
    endlocal
    exit /b 1
)

endlocal
exit /b 0
