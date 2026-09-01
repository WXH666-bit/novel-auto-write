$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$backendPath = Join-Path $projectRoot "backend"
$frontendIndex = Join-Path $projectRoot "frontend\dist\index.html"
$listenHost = if ($env:NOVEL_HOST) { $env:NOVEL_HOST } else { "127.0.0.1" }
$listenPort = if ($env:NOVEL_PORT) { [int]$env:NOVEL_PORT } else { 8000 }
$logLevel = if ($env:NOVEL_LOG_LEVEL) { $env:NOVEL_LOG_LEVEL.ToLowerInvariant() } else { "info" }
$authMode = if ($env:NOVEL_AUTH_MODE) { $env:NOVEL_AUTH_MODE.Trim().ToLowerInvariant() } else { "email" }

if ($authMode -notin @("email", "username")) {
    throw "NOVEL_AUTH_MODE 只能是 email 或 username"
}
$env:NOVEL_AUTH_MODE = $authMode

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "尚未找到 .venv，请先运行 .\setup.ps1"
}
if (-not (Test-Path -LiteralPath $frontendIndex)) {
    throw "前端尚未构建，请先运行 .\setup.ps1"
}

Set-Location -LiteralPath $backendPath
Write-Host "小说连续性工作台：http://$listenHost`:$listenPort"
Write-Host "账号登录模式：$authMode"
Write-Host "按 Ctrl+C 停止服务。"
& $venvPython -m uvicorn app.main:app --host $listenHost --port $listenPort --log-level $logLevel
