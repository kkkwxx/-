"""审核工作台、发布快照、公开栏目、关键词规则与 Dashboard Pydantic Schema。

所有接口均使用显式响应模型，确保 FastAPI `/openapi.json` 可直接生成前端 TypeScript 类型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# 通用标签映射（供前端中文展示参考）
# --------------------------------------------------------------------------- #

EVENT_TYPE_LABELS: dict[str, str] = {
    "deep_space_particle": "深空 / 粒子",
    "neo": "近地天体",
    "exoplanet": "新星球 / 类地行星",
    "bci_consciousness": "脑机 / 意识",
    "anomaly_psychic": "异常 / 灵能",
    "other": "其他",
}


# --------------------------------------------------------------------------- #
# 审核工作台：文章列表与详情
# --------------------------------------------------------------------------- #


class TitleCandidateOut(BaseModel):
    title: str
    style_tag: str = "反转式"
    hook_score: int = 3
    rationale: str = ""
    passed_check: bool = True
    check_errors: list[str] = Field(default_factory=list)
    effective_chars: int = 0


class ChecklistItem(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str = ""


class ClusterSiblingOut(BaseModel):
    id: int
    title: str
    source_level: str
    publisher_name: str | None = None
    original_url: str | None = None
    status: str
    created_at: datetime


class AiRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage: str
    model: str
    prompt_version: str
    tokens: int
    latency_ms: int
    status: str
    error: str | None = None
    created_at: datetime


class ReviewActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reviewer: str
    action: str
    before_json: dict[str, Any] | None = None
    after_json: dict[str, Any] | None = None
    note: str | None = None
    created_at: datetime


class ArticleListItem(BaseModel):
    id: int
    title: str
    title_final: str | None = None
    body_lead: str | None = None
    publisher_name: str | None = None
    source_level: str
    source_level_reason: str
    event_type: str | None = None
    content_nature: str | None = None
    is_event: bool | None = None
    event_confidence: float | None = None
    hook_score: int | None = None
    style_tag: str | None = None
    culture_tag: str | None = None
    review_priority: int
    risk_flags: list[str] = Field(default_factory=list)
    status: str
    reject_reason: str | None = None
    need_review: bool = True
    event_cluster_id: int | None = None
    published_at_raw: datetime | None = None
    fetched_at: datetime | None = None
    event_time: datetime | None = None
    first_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ArticleListResponse(BaseModel):
    items: list[ArticleListItem]
    total: int
    page: int
    page_size: int
    counts_by_status: dict[str, int] = Field(default_factory=dict)


class PublishedSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    event_cluster_id: int | None = None
    version: int
    title_final: str
    body_lead: str
    body_summary: str
    event_type: str
    source_name: str
    source_url: str
    source_level: str
    content_nature: str | None = None
    disclaimer: str | None = None
    style_tag: str | None = None
    hook_score: int | None = None
    culture_tag: str | None = None
    view_count: int = 0
    hot_score: int = 0
    cluster_article_count: int = 1
    rank_badge: str | None = None
    is_active: bool
    published_at: datetime
    unpublished_at: datetime | None = None
    operator: str


class VerificationOut(BaseModel):
    passed: bool
    hard_failed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class ArticleDetail(ArticleListItem):
    summary: str | None = None
    raw_title: str | None = None
    raw_summary: str | None = None
    full_text: str | None = None
    full_text_status: str | None = None
    raw_url: str | None = None
    original_url: str | None = None
    publisher_url: str | None = None
    resolve_status: str = "unresolved"
    entities: list[str] = Field(default_factory=list)
    event_action: str | None = None
    is_future_event: bool = False
    fact_anchor: str | None = None
    title_candidates: list[TitleCandidateOut] = Field(default_factory=list)
    body_summary: str | None = None
    evidence_notes: list[dict[str, Any]] = Field(default_factory=list)
    ai_generated_at: datetime | None = None
    verification: VerificationOut
    publish_checklist: list[ChecklistItem] = Field(default_factory=list)
    can_publish: bool = False
    cluster_siblings: list[ClusterSiblingOut] = Field(default_factory=list)
    ai_runs: list[AiRunOut] = Field(default_factory=list)
    review_actions: list[ReviewActionOut] = Field(default_factory=list)
    latest_published: PublishedSnapshotOut | None = None


class ArticleEditRequest(BaseModel):
    title_final: str | None = None
    body_lead: str | None = None
    body_summary: str | None = None
    fact_anchor: str | None = None
    style_tag: str | None = None
    hook_score: int | None = Field(default=None, ge=1, le=5)
    event_type: str | None = None
    content_nature: str | None = None
    culture_tag: str | None = None
    evidence_url: str | None = None
    evidence_note: str | None = None


class ReviewActionRequest(BaseModel):
    action: Literal["approve", "reject", "to_pending", "re_generate", "re_preprocess"]
    reject_reason: str | None = None
    note: str | None = None


class PublishRequest(BaseModel):
    disclaimer: str | None = None
    note: str | None = None


class BatchDeleteArticlesRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


class BatchDeleteSkippedItem(BaseModel):
    id: int
    reason: str


class BatchDeleteArticlesResponse(BaseModel):
    deleted_ids: list[int] = Field(default_factory=list)
    skipped: list[BatchDeleteSkippedItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 公开栏目展示页（/column，只读 published_article 快照）
# --------------------------------------------------------------------------- #


class ColumnArticleListResponse(BaseModel):
    items: list[PublishedSnapshotOut]
    total: int
    page: int
    page_size: int
    counts_by_type: dict[str, int] = Field(default_factory=dict)
    total_active: int = 0
    today_published: int = 0


class ColumnRankingItem(BaseModel):
    rank: int
    id: int
    title_final: str
    event_type: str
    source_name: str
    source_level: str
    hook_score: int | None = None
    view_count: int = 0
    cluster_article_count: int = 1
    hot_score: int
    rank_badge: str | None = None
    published_at: datetime


class ColumnRankingResponse(BaseModel):
    items: list[ColumnRankingItem]
    formula_desc: str
    updated_at: datetime


# --------------------------------------------------------------------------- #
# 关键词规则管理
# --------------------------------------------------------------------------- #


class KeywordRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    category: str
    pattern: str
    weight: int
    scope: str
    enabled: bool
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class KeywordRuleListResponse(BaseModel):
    items: list[KeywordRuleOut]
    total: int


class KeywordRuleCreate(BaseModel):
    type: Literal["exclude_hard", "exclude_soft", "anchor", "signal", "event_verb"]
    category: str = Field(min_length=1, max_length=32)
    pattern: str = Field(min_length=1)
    weight: int = 0
    scope: Literal["title", "title_summary", "all"] = "title_summary"
    enabled: bool = True
    note: str | None = None


class KeywordRuleUpdate(BaseModel):
    pattern: str | None = None
    weight: int | None = None
    enabled: bool | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# 运营监控 Dashboard
# --------------------------------------------------------------------------- #


class SourceAlertItem(BaseModel):
    id: int
    code: str
    name: str
    health_status: str
    consecutive_failures: int
    last_error_kind: str | None = None
    last_error: str | None = None


class DashboardOverview(BaseModel):
    pipeline_counts: dict[str, int]
    today_ingested: int
    today_published: int
    active_published_total: int
    today_tokens: int
    daily_token_budget: int
    token_usage_ratio: float
    source_health_counts: dict[str, int]
    unknown_domain_count: int
    alert_sources: list[SourceAlertItem]
    event_type_counts: dict[str, int]
