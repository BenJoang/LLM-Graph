$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopRoot = Join-Path $projectRoot "desktop"
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$requirements = Join-Path $projectRoot "requirements-LLMv1.txt"

function Find-Python312 {
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @($launcher.Source, "-3.12")
        }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @($python.Source)
        }
    }

    throw "Python 3.12 was not found. Install Python 3.12 from python.org and enable the Python Launcher or add python.exe to PATH."
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "Creating project virtual environment at .venv..."
    $pythonCommand = @(Find-Python312)
    $executable = $pythonCommand[0]
    $prefixArguments = @($pythonCommand | Select-Object -Skip 1)
    & $executable @prefixArguments -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv."
    }
}

Write-Host "Installing Python dependencies into .venv..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in .venv."
}
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python dependencies."
}

$node = Get-Command "node.exe" -ErrorAction SilentlyContinue
$npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
    throw "Node.js and npm were not found. Install the current Node.js LTS release and add it to PATH."
}

Write-Host "Installing desktop dependencies..."
& $npm.Source install --prefix $desktopRoot
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install desktop dependencies."
}

Write-Host ""
Write-Host "Setup complete. Start the GUI with .\start_gui.ps1 or start_gui.bat."
