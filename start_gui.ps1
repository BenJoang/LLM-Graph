$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopRoot = Join-Path $projectRoot "desktop"
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if ($env:LLM_GRAPH_PYTHON) {
    $configuredPython = $env:LLM_GRAPH_PYTHON
    if (-not [System.IO.Path]::IsPathRooted($configuredPython)) {
        $configuredPython = Join-Path $projectRoot $configuredPython
    }
    $pythonPath = [System.IO.Path]::GetFullPath($configuredPython)
} else {
    $pythonPath = $projectPython
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project Python was not found at '$pythonPath'. Run .\setup.ps1 first."
}

& $pythonPath -c "import sys; assert sys.version_info[:2] == (3, 12)"
if ($LASTEXITCODE -ne 0) {
    throw "LLM-Graph requires Python 3.12. Current interpreter: $pythonPath"
}

$node = Get-Command "node.exe" -ErrorAction SilentlyContinue
$npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
    throw "Node.js and npm were not found. Install Node.js and add it to PATH."
}

if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot "node_modules"))) {
    Write-Host "Installing desktop dependencies..."
    & $npm.Source install --prefix $desktopRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install desktop dependencies."
    }
}

$electronInstall = Join-Path $desktopRoot "node_modules\electron\install.js"
$electronPath = Join-Path $desktopRoot "node_modules\electron\path.txt"
if ((Test-Path -LiteralPath $electronInstall) -and -not (Test-Path -LiteralPath $electronPath)) {
    Write-Host "Installing Electron runtime..."
    if (-not $env:ELECTRON_MIRROR) {
        $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    }
    & $node.Source $electronInstall
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the Electron runtime."
    }
}

$env:LLM_GRAPH_PROJECT_ROOT = $projectRoot
$env:LLM_GRAPH_PYTHON = $pythonPath
& $npm.Source run dev --prefix $desktopRoot
if ($LASTEXITCODE -ne 0) {
    throw "The Electron development process exited with code $LASTEXITCODE."
}
