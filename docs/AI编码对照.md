# AI 编码对照手册

> 版本：v1.0
> 日期：2026-11-26
> 定位：**写代码时唯一需要打开的对照文档**。这里只放"必须遵守"与"踩过的坑"，细节一律指向源文档。
> 用法：动手前扫一遍 §2 与 §7；报错先查 §4；交付前过 §8 清单。

---

## 1. 项目速览

「神秘新闻」：采集泛宇宙/异常类事件新闻 → 规则过滤 → AI 加工（标题/导语/摘要）→ **全人工审核** → 内部发布 → 栏目展示。一周工期，交付代码仓库 + 演示视频。

技术栈一句话：Windows 本地；Docker 只跑 `pgvector/pgvector:pg16`；FastAPI + SQLAlchemy 2（同步）+ Alembic + APScheduler；React 18 + Vite + AntD 5 + Pro Components；百炼 OpenAI 兼容接口；`openapi-typescript` 生成前端类型。

**红线：常驻服务 = 0（除 PostgreSQL），新增基础设施 = 0。**

---

## 2. 编码硬约束（不可协商）

### 2.1 采集 HTTP

| # | 约束 | 违反后果 |
|---|---|---|
| 1 | Worker 进程内**全局共用一个 `httpx.Client`**（`max_connections=8`、`max_keepalive_connections=8`），禁止每次请求新建 | 实测握手失败率 40—60% |
| 2 | 所有抓取走 **tenacity 退避重试**：5 次、`wait_exponential(multiplier=0.6, min=0.6, max=4)`；可重试异常 `ConnectError`/`ConnectTimeout`/`ReadTimeout`/`RemoteProtocolError`，可重试状态码 429/500/502/503/504 | 单次成功率 3/5，带重试 5/5 |
| 3 | **不要**为握手问题加 TLS 版本配置（实测强制 TLS1.2 无改善） | 徒增复杂度 |
| 4 | 同源**串行** + 间隔 ≥1 s；源间并发 4 | Google News 连续请求会 429 |
| 5 | 探活（`validate`）**参数必须独立**：超时 8 s、重试 **1 次**、并发 4 | 复用采集参数最坏 800 s |
| 6 | `API 进程绝不发 HTTP 请求`；`POST /poll` 只插 `task_run(pending)`，Worker 捞 | 慢源会卡住整个后台 |

### 2.2 定级（最易错）

| # | 约束 |
|---|---|
| 1 | **出处域名按源类型取**：`google_news`/`api` → **只取 `publisher_url`**；`rss`/`manual` → `resolved_url`，空则 `url`；都取不到 → C |
| 2 | **聚合源绝不能用 `url` 兜底**（域名恒为 `google.com`） |
| 3 | 定级函数签名 `grade_article_source(item, source, rules)`，**必须传整个 item**，不能只传 `resolved_url`；`rules` 是 `source_domain_rule` 中 enabled 规则的域名→等级快照（显式传入，不在函数内部查库） |
| 4 | 域名归一 = eTLD+1 小写；**后缀精确匹配，禁止 `contains` / `LIKE '%...%'`**（否则 `notnasa.gov` 命中 `nasa.gov`） |
| 5 | `reason` 必须拼命中域名，格式 `rule{N}:{domain}`，审核台直接展示 |
| 6 | C 不是丢弃，是**进待定池**（fail-safe：判不准交给人） |

### 2.3 字段读写权限（三方可写，互不越界）

| 写入方 | 可写字段 |
|---|---|
| 后台 API | `code`、`name`、`type`、`level`、`url`、`query_template`、`poll_interval_min`、`enabled`、`expected_domains`、`title_suffix_rules`、`extra_headers`、`api_key_env`、`note`、`last_validated_at` |
| Worker 采集 | `etag`、`last_modified`、`last_poll_at`、`last_success_at`、`consecutive_failures`、`success_count`、`error_count`、`health_status`、`last_error`、`last_error_at`、`last_error_kind` |
| AI 层 | `effective_ratio_7d` |

**API 收到运行态字段必须显式报 `400 READONLY_FIELD`，禁止静默忽略**——否则人改一次配置就把"连续失败 9 次"冲成 0，故障被抹掉。

### 2.4 计数与状态

| # | 约束 |
|---|---|
| 1 | `consecutive_failures` **按任务级计数**：一次任务重试到最终仍失败才 +1；成功即清零 |
| 2 | 阈值硬编码：`≥3 → degraded`、`≥10 → down`、连续 3 次成功但 0 条 → `starved`；**但必须在详情页显示出来** |
| 3 | 401/403 从任意状态进 `auth_error`，只能人工重置，系统不自动重试到成功 |
| 4 | 重新启用（`enabled=false → true`）时状态置 **`degraded`**，不直接 `ok`；停用期间保留 `consecutive_failures` |
| 5 | `empty_feed`（200 + 0 条）与 `connect`/`http_4xx` 必须**分开显示**，处理动作相反 |
| 6 | 「重置健康度」清 `consecutive_failures` 与 `last_error*`，**保留** `success_count`/`error_count` |

### 2.5 AI 调用

| # | 约束 |
|---|---|
| 1 | 结构化调用**必须显式传** `response_format={"type":"json_object"}`；默认文本模式会返回带 json 代码围栏的内容，`json.loads` 直接崩 |
| 2 | 仍保留围栏剥离兜底；再过 Pydantic；失败重试 1 次；再失败转 `pending` |
| 3 | 模型：分类/抽取 `qwen3.8-flash`（`enable_thinking:false`）、生成 `qwen-plus`、难例 `qwen3.8-max`；**`qwen3-max` 不存在（404）**，`qwen-turbo` 限流（429） |

### 2.6 前端

| # | 约束 |
|---|---|
| 1 | 类型从 `/openapi.json` 生成（`npm run gen:api`），**禁止手抄 interface** |
| 2 | `/column` 栏目页**不 import 任何 pro-components**，只走原生 CSS + 少量 antd |
| 3 | `/admin/*` 与 `/column/*` 必须路由级 `React.lazy` 拆分（实测单 chunk 1.8 MB） |
| 4 | 状态标签显示**中文**，不显示枚举名 |

### 2.7 数据与迁移

| # | 约束 |
|---|---|
| 1 | 数据库存 UTC；`APP_TIMEZONE` 只影响展示与时效判断 |
| 2 | 迁移里 `CREATE EXTENSION IF NOT EXISTS pg_trgm;`，给 `article.title` 建 `gin_trgm_ops` 索引 |
| 3 | 删除有 `raw_item` 的源 → `409`，只能停用（否则历史新闻来源断链）；`code='manual'` 不可删 |
| 4 | 改域名规则后必须能触发**重算**（`article.source_level` 是落库值，不重算会新旧不一致） |

---

## 3. 模块状态

| 模块 | 文档 | 状态 |
|---|---|---|
| 总体架构 | [01-总体架构.md](./总体架构/01-总体架构.md) | v0.1 **冻结**，不改 |
| 技术栈 | [技术栈.md](./总体架构/技术栈.md) | v2.0 已定稿 |
| 信源管理 | [03-信源管理.md](./step01/03-信源管理.md) | v0.2 已定稿（19 接口 / 30 字段 / 6 态）；**后端已实现**（`app/api/sources.py`、`domain_rules.py`） |
| 采集 | [04-采集.md](./step01/04-采集.md) | v0.2 已定稿；**后端已实现**（`app/services/{fetch,adapters,collector,health,fulltext,validate,recalc}.py` + `app/worker.py`） |
| 采集数据测试 | [05-采集数据测试.md](./step01/05-采集数据测试.md) | 有效，"待收敛"部分以技术栈 §12 为准 |
| 预处理与事件判定 | [05-预处理与事件判定.md](./step01/05-预处理与事件判定.md) | **v0.1 已定稿**（排除词表基于 232 条实采；`is_event` 两段式 + 五类 + `review_priority`） |
| 去重与时效 | 待写（07） | — |
| AI 加工 | 待写（08） | — |
| 审核工作台 | 待写（09） | — |
| 发布与栏目 | 待写（10） | — |

---

## 4. 踩过的坑速查

完整记录见 [reference/问题与决策.md](./reference/问题与决策.md)。**命中就照做，不要重新试错。**

| 现象 | 编号 | 一句话 |
|---|---|---|
| `SSL: UNEXPECTED_EOF_WHILE_READING` 约 40—60% | A-01 | tenacity 退避重试 + 单 Client；**不是 TLS 问题** |
| phys.org 文章页 403 | A-02 | 回退 RSS 摘要，`full_text_status='blocked'`，不阻断流程 |
| `json.loads` 崩 / 返回带围栏 | A-03 | 强制 `response_format=json_object` |
| GN 条目数比实际多 2 条 | A-04 | 用 feedparser，别用正则扫 XML |
| GN 真实链接回溯不出来 | A-05 | 不回溯，读 `<source url>` 定级 |
| 摘要出现 `â€™` | A-06 | 假警报；显式 UTF-8 严格解码 |
| GN 连续请求 429 | A-07 | 同源串行 + 间隔 ≥1 s |
| `uv add` / `npm install` 网络失败 | A-08 | 重试循环，别重装整个环境 |
| 中文变乱码 / 文件带 BOM | A-09 | 用编辑工具改文件；必须用 shell 时用 `UTF8Encoding($false)` |
| 打包 chunk 1.8 MB 警告 | A-13 | 路由级懒加载 + `manualChunks` |
| docker compose 报 `project name must not be empty` | A-14 | 目录含中文时 compose 推不出项目名，compose 文件里必须写 `name:` |
| `alembic upgrade` 报 `UnicodeDecodeError: 'gbk'` | A-15 | Alembic 用 locale 编码读 `alembic.ini`，该文件注释必须全 ASCII |
| PowerShell `@'...'@` 里的双引号被吃掉 | A-16 | 长脚本一律先落盘再 `uv run python 文件`，不要塞 here-string |

---

## 5. 待你确认的开放决策

做到对应部分再定，不要提前实现。

| 编号 | 事项 | 影响 |
|---|---|---|
| B-05 | 标题 10—18 字口径（需求示例是 8—9 字，且未说含不含标点） | **阻塞** AI 加工 prompt、轻校验、评测集 |
| B-07 | 分类用 `qwen-flash`（10 tokens）还是 `qwen3.8-flash`（110 tokens） | 影响成本与 `.env` 默认值 |
| B-04 | phys.org 是否接受"仅摘要" | 影响演示口径 |
| B-06 | C 级源只做手工导入 | 影响待定池演示数据 |

---

## 6. 文档地图

```
docs/
├─ AI编码对照.md          ← 本文，编码时唯一入口
├─ background/
│  └─ 实习题目1.1.docx     ← 甲方基准需求（唯一权威，冲突以它为准）
├─ 总体架构/
│  ├─ 01-总体架构.md/.png/.svg
│  └─ 技术栈.md            ← v2.0 选型结论
├─ step01/
│  ├─ 03-信源管理.md       ← v0.2
│  ├─ 03-三级定级.svg / 03-继承还是重算.svg / 03-定级判定链.svg / 03-健康状态机.svg
│  ├─ 05-预处理与事件判定.md  ← v0.1，含 05-标题样本-2026-11-26.md
│  ├─ 04-采集.md
│  └─ 05-采集数据测试.md
└─ reference/              ← 过程材料，查证据用，不参与实现
   ├─ 技术栈选型评估.md
   ├─ 03-接口与页面-讨论稿.md（已并入 03，留档）
   ├─ 问题与决策.md
   └─ 任务拆解.md
```

---

## 7. 严禁事项

| 严禁 | 原因 |
|---|---|
| 引入 Celery / Redis / RSSHub / Miniflux / Firecrawl / Scrapy / Playwright | 已逐项评估并拒绝，理由见 [技术栈选型评估.md](./reference/技术栈选型评估.md) |
| 手抄前端 TS interface | 必须由 `openapi-typescript` 生成 |
| 自动发布任何内容 | 需求要求全人工审核；`risk_flags`/`hook_score` 只用于排序高亮 |
| 把灵异/灵能内容写成科学结论 | 必须带 `content_nature` 与免责声明 |
| 伪装浏览器 UA 绕 403 | ATel 实测浏览器 UA 也 403，是需凭据而非反爬 |
| 提交 `.env` / 真实 Key / `node_modules` / `storage/` | 仓库干净是验收项 |
| 在 API 进程里同步抓取 | 违反 §2.1 第 6 条 |

---

## 8. 交付前自检

1. `uv sync` → `uv run alembic upgrade head` → `uv run python -m app.seed` → 起 API/Worker/前端，全链路可跑
2. `/health` 200；`/docs` 可开；`npm run gen:api` 后 `tsc -b` 无错
3. `docker compose ps` **只有 `db` 一个容器**
4. `pytest` 全绿；测试不调真实百炼
5. `uv run python -m app.seed --verify` 对账表全 OK（含 6 类正反验证样本）
6. 连续跑 2 轮全源采集，`task_run` 无永久失败源
7. 演示主链路：采集 → 定级 → 过滤 → AI 加工 → 审核 → 发布 → 栏目，跑通
8. 仓库无 `.env`、无真实 Key、无 `node_modules`、无 `storage/`

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.1 | 2026-11-26 | 信源管理 + 采集后端落地（72 项 pytest 全绿）；定级签名补 `rules` 参数；新增 A-14/A-15/A-16 三条踩坑 |
