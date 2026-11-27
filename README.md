# 神秘新闻（Mystery News）

面向“神秘 / 前沿科学”选题的自动采集、AI 内容加工、动态风控与全人工审核发布系统：信源管理 → 采集定级 → 规则与两级去重 → 时效门禁 → AI 标题/导语/摘要加工与轻校验 → 全人工审核工作台 → 不可变发布快照 → 移动端风格栏目展示。

## 目录结构

```text
mystery-news/
├─ backend/            # FastAPI + SQLAlchemy 2 + Alembic（uv 管理）
│  ├─ app/
│  │  ├─ core/         # 配置、数据库会话、日志、统一错误
│  │  ├─ models/       # 13 张业务表（source / raw_item / article / event_cluster / published_article / risk_policy_config …）
│  │  ├─ services/     # 采集、定级、去重时效、AI 加工、轻校验、动态风控策略
│  │  ├─ api/          # 审核工作台、信源管理、风控策略、公开栏目与监控 API
│  │  ├─ seed.py       # 幂等种子（支持 --verify 对账与 --demo 10 条验收样例）
│  │  └─ worker.py     # 常驻调度进程（6 个防重入/周期回收 Job）
│  ├─ alembic/         # 0001–0004 线性迁移（含 pg_trgm 与标题 GIN 索引）
│  └─ tests/           # pytest 全量单测与集成测试（含需求 §10 全部 10 条验收用例）
├─ frontend/           # React 18 + Vite + TypeScript + AntD 5（路由级懒加载，C 端零 AntD）
├─ fixtures/           # source_seed / domain_rule_seed / keyword_rule_seed / eval/acceptance_10
├─ docs/               # 需求与架构设计文档
├─ scripts/            # start.ps1 / stop.ps1
└─ docker-compose.yml  # 严格只起一个 PostgreSQL 16 + pg_trgm 容器
```

## 前置条件

- Docker Desktop（跑 PostgreSQL 16 + `pg_trgm`，宿主端口 `5433`）
- `uv`（Python 3.12+，依赖由 `uv sync` 装进 `backend\.venv`）
- Node.js 20+（用于运行 `frontend`）
- 首次运行前需复制环境变量：`Copy-Item .env.example .env`，并为后台登录配置 `ADMIN_PASSWORD_HASH`（使用 `start.ps1` 会自动为本地开发初始化默认密码 `admin123`，生产环境请务必修改）

## 启动步骤（PowerShell）

### 方式一：一键启动（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

### 方式二：分步手动启动

```powershell
Copy-Item .env.example .env                        # 首次
docker compose up -d --wait db                     # 只允许 db 一个容器
cd backend
uv sync
uv run python -m app.seed --hash-password admin123 # 生成哈希填入 .env 的 ADMIN_PASSWORD_HASH
uv run alembic upgrade head                        # 建全部 13 张业务表 + pg_trgm + 索引
uv run python -m app.seed --demo                   # 灌入信源 / 域名规则 / 关键词规则 + 10 条验收演示样例
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 18000  # 窗口 1：API（避开 Windows 8000 保留端口）
uv run python -m app.worker                                         # 窗口 2：Worker
cd ..\frontend
npm install && npm run gen:api && npm run dev -- --host             # 窗口 3：React 管理台 + 移动端栏目页
```

- 公开栏目页（移动端窄屏风）：`http://localhost:5173/column`
- 审核运营台（原文对照 + 7 项 Checklist）：`http://localhost:5173/admin/review`
- 风控策略与规则（三档严厉度开关 + 动态阈值）：`http://localhost:5173/admin/rules`
- 运营监控总览：`http://localhost:5173/admin/dashboard`
- API 文档：`http://127.0.0.1:18000/docs`

## 常用命令

| 目的 | 命令（在 `backend` 下执行） |
|---|---|
| 建表 / 升级 | `uv run alembic upgrade head` |
| 检查迁移与模型是否一致 | `uv run alembic check` |
| 回滚一步 | `uv run alembic downgrade -1` |
| 灌种子（含演示样例） | `uv run python -m app.seed --demo` |
| 种子 + 探活对账 | `uv run python -m app.seed --verify` |
| 生成管理员密码哈希 | `uv run python -m app.seed --hash-password <明文>` |
| 起 API | `uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 18000` |
| 起 Worker | `uv run python -m app.worker` |
| 跑全量测试 | `uv run pytest` |
| 停数据库 | `docker compose down` |

## 红线

- **只允许 `db` 一个容器**：不引入 Redis / Celery / Elasticsearch / Nginx。
- 仓库**不含** `.env`、`node_modules/`、`dist/`、`storage/`（已在 `.gitignore`）。
- 数据库存 UTC；时区只影响展示与时效判断。
