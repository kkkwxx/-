"""信源管理 + 采集的 Pydantic v2 模型（03 §8.3 / 04 §12）。

两条硬约束：

1. **响应全部是明确的 model**，不返回裸 dict —— 前端 `openapi-typescript`
   靠它生成类型（03 §8.1）。
2. 时间一律 UTC，序列化成 ISO 8601 带 ``Z``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def iso_utc(value: datetime | None) -> str | None:
    """datetime → ISO 8601（UTC，带 Z）。库里存 UTC，naive 值按 UTC 处理。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UTCDateTime = Annotated[
    datetime, PlainSerializer(iso_utc, return_type=str, when_used="json")
]
UTCDateTimeOpt = UTCDateTime | None


# --------------------------------------------------------------------------- #
# 枚举（与 app.models.source 的 CHECK 约束一一对应）
# --------------------------------------------------------------------------- #
class SourceType(str, Enum):
    rss = "rss"
    google_news = "google_news"
    api = "api"
    manual = "manual"


class Level(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class HealthStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"
    down = "down"
    auth_error = "auth_error"
    starved = "starved"
    disabled = "disabled"


class ErrorKind(str, Enum):
    connect = "connect"
    http_4xx = "http_4xx"
    http_5xx = "http_5xx"
    auth = "auth"
    parse = "parse"
    empty_feed = "empty_feed"


class SortBy(str, Enum):
    code = "code"
    level = "level"
    last_success_at = "last_success_at"
    success_rate_7d = "success_rate_7d"
    items_7d = "items_7d"


class TaskType(str, Enum):
    poll = "poll"
    fetch_full_text = "fetch_full_text"
    recalc_levels = "recalc_levels"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"


# 03 §3.2：这些字段只有 Worker / AI 层能写，PATCH 收到必须报 READONLY_FIELD
RUNTIME_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "etag",
        "last_modified",
        "last_poll_at",
        "last_success_at",
        "consecutive_failures",
        "success_count",
        "error_count",
        "health_status",
        "last_error",
        "last_error_at",
        "last_error_kind",
        "effective_ratio_7d",
        "created_at",
        "updated_at",
    }
)


class ORMModel(BaseModel):
    """所有出参基类：允许直接从 ORM 对象构造。"""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# 通用小件
# --------------------------------------------------------------------------- #
class Warning(BaseModel):
    """S6 新建时的"未验证"提示（03 §8.4）。"""

    code: str
    message: str


class MessageResponse(BaseModel):
    ok: bool = True
    message: str


# --------------------------------------------------------------------------- #
# S3 详情里的统计与阈值
# --------------------------------------------------------------------------- #
class SourceStats(BaseModel):
    success_rate_7d: float | None = None
    items_7d: int = 0
    effective_ratio_7d: float | None = None
    empty_runs_7d: int = 0
    avg_duration_ms_7d: int | None = None
    # PG 才有 within_group，这里按 7 天运行记录在 Python 侧算
    p95_duration_ms_7d: int | None = None
    last_run_at: UTCDateTimeOpt = None


class SourceThresholds(BaseModel):
    degraded: int
    down: int
    starved: int


# --------------------------------------------------------------------------- #
# S1 列表
# --------------------------------------------------------------------------- #
class SourceListItem(ORMModel):
    id: int
    code: str
    name: str
    type: SourceType
    level: Level
    url: str | None = None
    api_key_env: str | None = None
    api_key_mask: str | None = None
    key_configured: bool = False
    note: str | None = None
    poll_interval_min: int
    enabled: bool
    health_status: HealthStatus
    consecutive_failures: int
    last_poll_at: UTCDateTimeOpt = None
    last_success_at: UTCDateTimeOpt = None
    last_validated_at: UTCDateTimeOpt = None
    success_rate_7d: float | None = None
    avg_duration_ms_7d: int | None = None
    items_7d: int = 0
    effective_ratio_7d: float | None = None
    last_error_kind: ErrorKind | None = None
    last_error: str | None = None
    needs_attention: bool = False


class SourceListResponse(BaseModel):
    items: list[SourceListItem]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# S2 顶部 KPI
# --------------------------------------------------------------------------- #
class SourceSummary(BaseModel):
    total: int
    ok_count: int
    needs_attention_count: int
    disabled_count: int
    today_items: int
    last_round_at: UTCDateTimeOpt = None
    unknown_domain_count: int


# --------------------------------------------------------------------------- #
# S3 详情（30 个存储字段 + 派生字段）
# --------------------------------------------------------------------------- #
class SourceDetail(ORMModel):
    # ---- 人可写 ----
    id: int
    code: str
    name: str
    type: SourceType
    level: Level
    url: str | None = None
    query_template: str | None = None
    poll_interval_min: int
    enabled: bool
    expected_domains: list[str] = Field(default_factory=list)
    title_suffix_rules: list[str] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_key_env: str | None = None
    api_key_mask: str | None = None
    note: str | None = None
    last_validated_at: UTCDateTimeOpt = None

    # ---- Worker 可写（运行态）----
    etag: str | None = None
    last_modified: str | None = None
    last_poll_at: UTCDateTimeOpt = None
    last_success_at: UTCDateTimeOpt = None
    consecutive_failures: int = 0
    success_count: int = 0
    error_count: int = 0
    health_status: HealthStatus
    last_error: str | None = None
    last_error_at: UTCDateTimeOpt = None
    last_error_kind: ErrorKind | None = None

    # ---- AI 层 / seed / 系统 ----
    effective_ratio_7d: float | None = None
    expected_status_on_first_run: str | None = None
    created_at: UTCDateTimeOpt = None
    updated_at: UTCDateTimeOpt = None

    # ---- 派生 ----
    key_configured: bool = False
    etag_cached: bool = False
    thresholds: SourceThresholds
    stats: SourceStats


class SourceCreateResponse(SourceDetail):
    """S6 的响应：详情 + 可选的"未验证"提示。"""

    warning: Warning | None = None


# --------------------------------------------------------------------------- #
# S6 新建 / S7 修改
# --------------------------------------------------------------------------- #
class SourceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    type: SourceType
    level: Level
    url: str | None = None
    query_template: str | None = None
    poll_interval_min: int = Field(default=30, ge=1)
    enabled: bool = True
    expected_domains: list[str] = Field(default_factory=list)
    title_suffix_rules: list[str] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_key_env: str | None = None
    api_key: str | None = None
    note: str | None = None


class SourceUpdate(BaseModel):
    """S7 请求体。

    **故意不加 ``extra="forbid"``**：运行态字段要能被收到，再由路由显式报
    ``400 READONLY_FIELD``（03 §3.2 要求"不许静默忽略"）。路由用
    ``model_fields_set`` 判断哪些字段被显式传递。
    """

    # ---- 人可写 ----
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    type: SourceType | None = None
    level: Level | None = None
    url: str | None = None
    query_template: str | None = None
    poll_interval_min: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    expected_domains: list[str] | None = None
    title_suffix_rules: list[str] | None = None
    extra_headers: dict[str, str] | None = None
    api_key_env: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    note: str | None = None

    # ---- 运行态：只为"收到后报错"，路由不会写它们 ----
    id: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_poll_at: UTCDateTimeOpt = None
    last_success_at: UTCDateTimeOpt = None
    consecutive_failures: int | None = None
    success_count: int | None = None
    error_count: int | None = None
    health_status: HealthStatus | None = None
    last_error: str | None = None
    last_error_at: UTCDateTimeOpt = None
    last_error_kind: ErrorKind | None = None
    effective_ratio_7d: float | None = None
    created_at: UTCDateTimeOpt = None
    updated_at: UTCDateTimeOpt = None


class ToggleRequest(BaseModel):
    enabled: bool


# --------------------------------------------------------------------------- #
# S4 运行记录 / S5 采回条目
# --------------------------------------------------------------------------- #
class RunItem(ORMModel):
    id: int
    started_at: UTCDateTimeOpt = None
    finished_at: UTCDateTimeOpt = None
    status: TaskStatus
    fetched: int = 0
    duration_ms: int | None = None
    attempts: int | None = None
    error_kind: str | None = None
    error: str | None = None


class TaskRunListResponse(BaseModel):
    items: list[RunItem]
    total: int
    page: int
    page_size: int


class CollectedItem(BaseModel):
    raw_item_id: int
    raw_title: str
    fetched_at: UTCDateTimeOpt = None
    article_id: int | None = None
    source_level: Level | None = None
    source_level_reason: str | None = None
    is_event: bool | None = None
    event_type: str | None = None
    status: str | None = None
    reject_reason: str | None = None


# --------------------------------------------------------------------------- #
# S10 / C1 动作
# --------------------------------------------------------------------------- #
class PollResponse(BaseModel):
    task_run_id: int
    status: str


class RecalcResponse(BaseModel):
    task_run_id: int
    status: str


# --------------------------------------------------------------------------- #
# V1 / V2 探活
# --------------------------------------------------------------------------- #
class PreviewItem(BaseModel):
    title: str
    published: str | None = None
    link: str | None = None


class ValidateRequest(BaseModel):
    """V2 收的是**配置**而不是 id：支持"新建时还没保存"的场景。"""

    source_type: SourceType
    url: str | None = None
    query_template: str | None = None
    extra_headers: dict[str, str] | None = None
    api_key_env: str | None = None
    source_id: int | None = None


class ValidateResult(BaseModel):
    source_id: int | None = None
    code: str | None = None
    ok: bool
    http_status: int | None = None
    entries: int = 0
    duration_ms: int = 0
    error_kind: str | None = None
    error: str | None = None
    suggestion: str = ""
    preview: list[PreviewItem] = Field(default_factory=list)


class ValidateResponse(BaseModel):
    results: list[ValidateResult]
    ok_count: int
    fail_count: int


# --------------------------------------------------------------------------- #
# 域名规则 R1—R5
# --------------------------------------------------------------------------- #
class DomainRuleItem(ORMModel):
    id: int
    domain: str
    level: Level
    note: str | None = None
    enabled: bool
    # 暂不可用（已与需求方确认）：03 §8.3 要"近 30 天命中数"，但表结构没有计数列，
    # 靠 article.source_level_reason 做 LIKE 统计既不准又要扫大表。**恒为 null**，
    # 前端显示「—」，不要显示 0（0 会被读成"没有命中"）。
    hit_count_30d: int | None = None


class DomainRuleCreateResponse(DomainRuleItem):
    recalc_suggested: bool = True


class DomainRuleCreate(BaseModel):
    domain: str = Field(min_length=1, max_length=255)
    level: Level
    note: str | None = None


class DomainRuleUpdate(BaseModel):
    level: Level | None = None
    note: str | None = None
    enabled: bool | None = None


class DomainRuleListResponse(BaseModel):
    items: list[DomainRuleItem]
    total: int
    page: int
    page_size: int


class UnknownDomainItem(ORMModel):
    domain: str
    hit_count: int
    first_seen_at: UTCDateTimeOpt = None
    last_seen_at: UTCDateTimeOpt = None
    sample_title: str | None = None
    sample_source_code: str | None = None


class UnknownDomainListResponse(BaseModel):
    items: list[UnknownDomainItem]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# 手工导入（04 §12）
# --------------------------------------------------------------------------- #
class ManualImportRequest(BaseModel):
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str | None = None
    published_at: UTCDateTimeOpt = None
    source_code: str = "manual"
    note: str | None = None


class ManualImportResponse(BaseModel):
    raw_item_id: int
    article_id: int
    source_level: Level
    source_level_reason: str
