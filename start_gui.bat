@echo off
setlocal

title LLM-Graph GUI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_gui.ps1"
if errorlevel 1 (
    echo.
    echo LLM-Graph GUI failed to start. Review the error above.
    pause
    endlocal
    exit /b 1
)

endlocal
exit /b 0
