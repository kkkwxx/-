# 停止开发进程（不动数据库，数据保留）
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot

Write-Host '== 停止 API / Worker / Vite ==' -ForegroundColor Cyan
foreach ($pattern in @('uvicorn app.main:app', 'app.worker', 'vite')) {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pattern*" } |
        ForEach-Object {
            Write-Host ("kill {0} {1}" -f $_.ProcessId, $pattern)
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

Write-Host '== 停止数据库容器 ==' -ForegroundColor Cyan
Set-Location $root
docker compose stop db

Write-Host '完成（数据卷保留，如需清库用 scripts/reset-db.ps1）' -ForegroundColor Green
