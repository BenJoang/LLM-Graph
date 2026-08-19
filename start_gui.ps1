$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopRoot = Join-Path $projectRoot "desktop"

if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot "node_modules"))) {
    Write-Host "Installing desktop dependencies..."
    npm install --prefix $desktopRoot
}

$electronInstall = Join-Path $desktopRoot "node_modules\electron\install.js"
$electronPath = Join-Path $desktopRoot "node_modules\electron\path.txt"
if ((Test-Path -LiteralPath $electronInstall) -and -not (Test-Path -LiteralPath $electronPath)) {
    Write-Host "Installing Electron runtime..."
    if (-not $env:ELECTRON_MIRROR) {
        $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    }
    node $electronInstall
}

$env:LLM_GRAPH_PROJECT_ROOT = $projectRoot
npm run dev --prefix $desktopRoot
