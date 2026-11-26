# 一键启动：依赖 → 迁移 → seed → API / Worker / 前端
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '== 1/4 启动 PostgreSQL（只允许 db 一个容器）==' -ForegroundColor Cyan
docker compose up -d db
docker compose ps

Write-Host '== 2/4 同步后端依赖 ==' -ForegroundColor Cyan
Push-Location backend
uv sync

Write-Host '== 3/4 迁移 + seed ==' -ForegroundColor Cyan
uv run alembic upgrade head
uv run python -m app.seed
Pop-Location

Write-Host '== 4/4 启动 API / Worker / 前端（各开一个窗口）==' -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command', "cd '$root\backend'; uv run uvicorn app.main:app --reload --port 8000"
)
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command', "cd '$root\backend'; uv run python -m app.worker"
)
if (Test-Path "$root\frontend\package.json") {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-Command', "cd '$root\frontend'; npm run dev"
    )
} else {
    Write-Host '前端尚未创建，跳过。' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'API   http://localhost:8000/docs' -ForegroundColor Green
Write-Host '前端  http://localhost:5173/admin/sources' -ForegroundColor Green
Write-Host '停止：.\scripts\stop.ps1' -ForegroundColor Green
