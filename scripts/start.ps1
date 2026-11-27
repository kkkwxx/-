# 一键启动：环境检查 → PostgreSQL(等待就绪) → 迁移/seed → 前端依赖 → API / Worker / 前端
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 兼容 uv 装在 ~/.local/bin 或 ~/.cargo/bin 但未进当前终端 PATH 的情况
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
    )) {
        if (Test-Path $candidate) {
            $env:Path = "$(Split-Path $candidate);$env:Path"
            $uvCmd = $candidate
            break
        }
    }
    if (-not $uvCmd) {
        throw '找不到 uv，请先安装 uv 或将其加入 PATH。'
    }
}

Write-Host '== 0/5 检查 .env 配置 ==' -ForegroundColor Cyan
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root '.env.example') $envFile
    Write-Host '已从 .env.example 自动创建 .env' -ForegroundColor Yellow
}

Write-Host '== 1/5 启动 PostgreSQL 并等待健康检查（只允许 db 一个容器）==' -ForegroundColor Cyan
docker compose up -d --wait db
docker compose ps

Write-Host '== 2/5 同步后端依赖并检查管理员密码哈希 ==' -ForegroundColor Cyan
Push-Location backend
& $uvCmd sync

$envText = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
if ($envText -match '(?m)^ADMIN_PASSWORD_HASH=\s*$') {
    $defaultHash = (& $uvCmd run python -m app.seed --hash-password admin123).Trim()
    $envText = $envText -replace '(?m)^ADMIN_PASSWORD_HASH=\s*$', "ADMIN_PASSWORD_HASH=$defaultHash"
    [System.IO.File]::WriteAllText($envFile, $envText, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host '已为本地开发环境自动初始化 ADMIN_PASSWORD_HASH（账号 admin / 默认密码 admin123，生产请修改）' -ForegroundColor Yellow
}

Write-Host '== 3/5 迁移 + seed（含演示验收样例）==' -ForegroundColor Cyan
& $uvCmd run alembic upgrade head
& $uvCmd run python -m app.seed --demo
Pop-Location

Write-Host '== 4/5 检查前端依赖 ==' -ForegroundColor Cyan
$feDir = Join-Path $root 'frontend'
if ((Test-Path (Join-Path $feDir 'package.json')) -and -not (Test-Path (Join-Path $feDir 'node_modules'))) {
    Push-Location $feDir
    npm install
    Pop-Location
}

Write-Host '== 5/5 启动 API / Worker / 前端（各开一个窗口）==' -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command', "cd '$root\backend'; & '$uvCmd' run uvicorn app.main:app --reload --host 127.0.0.1 --port 18000"
)
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command', "cd '$root\backend'; & '$uvCmd' run python -m app.worker"
)
if (Test-Path (Join-Path $feDir 'package.json')) {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-Command', "cd '$feDir'; npm run dev -- --host"
    )
} else {
    Write-Host '前端尚未创建，跳过。' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'API 文档      http://127.0.0.1:18000/docs' -ForegroundColor Green
Write-Host '审核运营台    http://localhost:5173/admin/review  (账号 admin / 默认密码 admin123)' -ForegroundColor Green
Write-Host '风控策略配置  http://localhost:5173/admin/rules' -ForegroundColor Green
Write-Host '公开栏目页    http://localhost:5173/column' -ForegroundColor Green
Write-Host '停止全部服务：.\scripts\stop.ps1' -ForegroundColor Green
