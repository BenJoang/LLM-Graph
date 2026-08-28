@echo off
setlocal

for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"
set "DESKTOP_ROOT=%PROJECT_ROOT%\desktop"

title LLM-Graph GUI
pushd "%PROJECT_ROOT%"

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm.cmd was not found. Install Node.js and add it to PATH.
    goto :failed
)

where node.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] node.exe was not found. Install Node.js and add it to PATH.
    goto :failed
)

if defined LLM_GRAPH_PYTHON (
    if not exist "%LLM_GRAPH_PYTHON%" (
        echo [ERROR] LLM_GRAPH_PYTHON does not point to an existing file:
        echo         %LLM_GRAPH_PYTHON%
        goto :failed
    )
) else (
    where conda.exe >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] conda.exe was not found. Add Conda to PATH or set LLM_GRAPH_PYTHON.
        goto :failed
    )

    conda.exe run -n LLMv1 python --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] The Conda environment LLMv1 is unavailable.
        echo         Create it first or set LLM_GRAPH_PYTHON to a Python executable.
        goto :failed
    )
)

if not exist "%DESKTOP_ROOT%\node_modules" (
    echo Installing desktop dependencies...
    call npm.cmd install --prefix "%DESKTOP_ROOT%"
    if errorlevel 1 goto :failed
)

set "ELECTRON_INSTALL=%DESKTOP_ROOT%\node_modules\electron\install.js"
set "ELECTRON_PATH=%DESKTOP_ROOT%\node_modules\electron\path.txt"

if exist "%ELECTRON_INSTALL%" (
    if not exist "%ELECTRON_PATH%" (
        echo Installing Electron runtime...
        if not defined ELECTRON_MIRROR set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
        node.exe "%ELECTRON_INSTALL%"
        if errorlevel 1 goto :failed
    )
)

set "LLM_GRAPH_PROJECT_ROOT=%PROJECT_ROOT%"
call npm.cmd run dev --prefix "%DESKTOP_ROOT%"
if errorlevel 1 goto :failed

popd
endlocal
exit /b 0

:failed
echo.
echo LLM-Graph GUI failed to start. Review the error above.
pause
popd
endlocal
exit /b 1
