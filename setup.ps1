$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "正在创建 Python 虚拟环境..."
    python -m venv (Join-Path $projectRoot ".venv")
}

Write-Host "正在安装 Python 依赖..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements-dev.txt")

$frontendPath = Join-Path $projectRoot "frontend"
if (Test-Path -LiteralPath (Join-Path $frontendPath "package.json")) {
    Write-Host "正在安装前端依赖..."
    npm --prefix $frontendPath install
    Write-Host "正在构建前端..."
    npm --prefix $frontendPath run build
}

Write-Host "环境准备完成。运行 .\start.ps1 启动应用。"
