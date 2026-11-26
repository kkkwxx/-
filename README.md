# 神秘新闻 MVP

面向"神秘 / 前沿科学"选题的自动采集与审核发布系统：信源管理 → 采集 → 定级 → 审核 → 发布，全流程可追溯。

## 目录结构

```text
D:\实习
├─ backend\            # FastAPI + SQLAlchemy 2 + Alembic（uv 管理）
│  ├─ app\
│  │  ├─ core\         # 配置、数据库会话、日志、错误
│  │  ├─ models\       # 7 张业务表（source / raw_item / article …）
│  │  ├─ services\     # 采集、定级、探活、正文抽取、健康度
│  │  ├─ api\          # 后台接口
│  │  ├─ seed.py       # 信源与域名规则种子
│  │  └─ worker.py     # 常驻调度进程
│  ├─ alembic\         # 迁移（含 pg_trgm 扩展与标题 GIN 索引）
│  └─ tests\           # pytest + fixtures（离线样本）
├─ fixtures\           # source_seed.yaml / domain_rule_seed.yaml
├─ docs\               # 需求与设计文档（只读）
├─ scripts\            # start.ps1 / stop.ps1
└─ docker-compose.yml  # 只起一个 PostgreSQL 容器
```

## 前置条件

- Docker Desktop（跑 PostgreSQL 16 + pg_trgm，宿主端口 5433）
- uv（Python 3.12+，依赖由 `uv sync` 装进 `backend\.venv`）
- Node.js 20+（前端，尚未创建时可跳过）
- 先复制环境变量：`Copy-Item .env.example .env`，再按需改 `ADMIN_PASSWORD_HASH`

## 启动步骤（PowerShell）

```powershell
Copy-Item .env.example .env            # 首次
docker compose up -d db                # 只允许 db 一个容器
cd backend
uv sync
uv run alembic upgrade head            # 建 7 张表 + pg_trgm + ix_article_title_trgm
uv run python -m app.seed              # 20 条源 + 24 条域名规则（幂等）
uv run uvicorn app.main:app --reload --port 8000   # 窗口 1：API
uv run python -m app.worker                        # 窗口 2：Worker
```

一键版：`powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1`。

## 种子与探活对账

- `uv run python -m app.seed`：按 `code` / `domain` upsert，**已存在只补缺失字段**，不会冲掉后台改过的配置。
- `uv run python -m app.seed --verify`：灌完种子后逐源探活，与 `expected_status_on_first_run` 比对，打印 source / expected / actual / match 对账表，用来证明系统能识别"坏源"（403、404、空壳 feed）。不匹配时退出码为 1。
- `uv run python -m app.seed --hash-password <明文>`：生成 `ADMIN_PASSWORD_HASH` 用的 bcrypt 哈希。
## 常用命令

| 目的 | 命令（在 `backend` 下执行） |
|---|---|
| 建表 / 升级 | `uv run alembic upgrade head` |
| 检查迁移与模型是否一致 | `uv run alembic check` |
| 回滚一步 | `uv run alembic downgrade -1` |
| 灌种子 | `uv run python -m app.seed` |
| 种子 + 探活对账 | `uv run python -m app.seed --verify` |
| 起 API | `uv run uvicorn app.main:app --reload --port 8000` |
| 起 Worker | `uv run python -m app.worker` |
| 跑测试 | `uv run pytest` |
| 停数据库 | `docker compose down` |

## 红线

- **只允许 `db` 一个容器**：不引入 Redis / Celery / Elasticsearch / Nginx。
- 仓库**不含** `.env`、`node_modules/`、`storage/`（已在 `.gitignore`）。
- 数据库存 UTC；时区只影响展示与时效判断。
