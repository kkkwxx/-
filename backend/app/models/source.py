"""信源、域名规则、未收录域名。字段口径见 03-信源管理 §3.1 / §6.4 / §6.6。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

SOURCE_TYPES = ("rss", "google_news", "api", "manual")
SOURCE_LEVELS = ("A", "B", "C")
HEALTH_STATUSES = ("ok", "degraded", "down", "auth_error", "starved", "disabled")
ERROR_KINDS = ("connect", "http_4xx", "http_5xx", "auth", "parse", "empty_feed")


class Source(Base, TimestampMixin):
    """信源配置 + 运行态。写入方严格分离，见 03 §3.2。"""

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ---- 人可写（后台 API）----
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[str] = mapped_column(String(1), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    query_template: Mapped[str | None] = mapped_column(Text)
    poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expected_domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    title_suffix_rules: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    extra_headers: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    api_key_env: Mapped[str | None] = mapped_column(String(64))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_mask: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)

    # ---- 人可写（探活时写）----
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- Worker 可写（运行态）----
    etag: Mapped[str | None] = mapped_column(String(256))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_kind: Mapped[str | None] = mapped_column(String(16))

    # ---- AI 层可写 ----
    effective_ratio_7d: Mapped[float | None] = mapped_column(Float)

    # ---- seed 专用（只用于 --verify 对账）----
    expected_status_on_first_run: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        CheckConstraint(
            "type IN ('rss','google_news','api','manual')", name="ck_source_type"
        ),
        CheckConstraint("level IN ('A','B','C')", name="ck_source_level"),
        CheckConstraint(
            "health_status IN ('ok','degraded','down','auth_error','starved','disabled')",
            name="ck_source_health",
        ),
        CheckConstraint(
            "last_error_kind IS NULL OR last_error_kind IN "
            "('connect','http_4xx','http_5xx','auth','parse','empty_feed')",
            name="ck_source_error_kind",
        ),
        Index("ix_source_health_enabled", "health_status", "enabled"),
    )


class SourceDomainRule(Base, TimestampMixin):
    """域名 → 等级规则表。命中即用，未命中走兜底（03 §6.1 第 ④ 步）。"""

    __tablename__ = "source_domain_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    level: Mapped[str] = mapped_column(String(1), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("level IN ('A','B','C')", name="ck_domain_rule_level"),
    )


class UnknownDomain(Base):
    """未收录域名。规则 6 触发时 upsert，运营一键归类后删除（03 §6.6）。"""

    __tablename__ = "unknown_domain"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_title: Mapped[str | None] = mapped_column(Text)
    sample_source_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("domain", name="uq_unknown_domain_domain"),
        Index("ix_unknown_domain_hit", "hit_count"),
    )
