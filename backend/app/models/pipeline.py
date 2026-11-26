"""原始条目与文章骨架。字段口径见 01-总体架构 §6、03 §6.8、04 §10/§11。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

FULL_TEXT_STATUSES = ("pending", "ok", "blocked", "thin", "skipped")


class RawItem(Base):
    """采集落地。原样保留，不做任何有损操作（04 §3 第 1 条）。"""

    __tablename__ = "raw_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_url: Mapped[str | None] = mapped_column(Text)
    publisher_url: Mapped[str | None] = mapped_column(Text)
    publisher_name: Mapped[str | None] = mapped_column(String(255))
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_summary: Mapped[str | None] = mapped_column(Text)
    content_encoded: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str | None] = mapped_column(Text)
    full_text_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    raw_html: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_raw: Mapped[str | None] = mapped_column(String(128))
    author: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        # 入口去重的兜底约束（04 §10.1 第 5 步）
        UniqueConstraint("source_id", "url_hash", name="uq_raw_item_source_url"),
        CheckConstraint(
            "full_text_status IN ('pending','ok','blocked','thin','skipped')",
            name="ck_raw_item_ft_status",
        ),
        Index("ix_raw_item_ft_status", "full_text_status", "fetched_at"),
        Index("ix_raw_item_url_hash", "url_hash"),
    )


class Article(Base, TimestampMixin):
    """标准化与加工结果。采集阶段只写骨架字段（04 §16）。"""

    __tablename__ = "article"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_item.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    publisher_name: Mapped[str | None] = mapped_column(String(255))
    publisher_url: Mapped[str | None] = mapped_column(Text)
    original_url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)

    # ---- 定级结果（03 §6.8）----
    source_level: Mapped[str] = mapped_column(String(1), nullable=False, default="C")
    source_level_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    resolve_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unresolved"
    )

    # ---- 去重指纹（04 §10.1 第 3 步）----
    url_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    title_fingerprint: Mapped[str | None] = mapped_column(Text)
    dup_sources: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)

    # ---- 下游字段（05 起写入，这里先建列，避免后续迁移改表）----
    is_event: Mapped[bool | None] = mapped_column(Boolean)
    event_type: Mapped[str | None] = mapped_column(String(32))
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_nature: Mapped[str | None] = mapped_column(String(32))
    # 05 §6.4：AI 给的事件置信度，进 candidate 的门槛见 EVENT_CONFIDENCE_MIN
    event_confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    risk_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    review_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ingested")
    reject_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("source_level IN ('A','B','C')", name="ck_article_level"),
        CheckConstraint(
            "resolve_status IN ('resolved','unresolved')", name="ck_article_resolve"
        ),
        # 05 §7.1：五类 + other（架构图明确要求 other 兜底），NULL = 未判定
        CheckConstraint(
            "event_type IS NULL OR event_type IN ('deep_space_particle','neo',"
            "'exoplanet','bci_consciousness','anomaly_psychic','other')",
            name="ck_article_event_type",
        ),
        CheckConstraint(
            "status IN ('ingested','rule_rejected','duplicate','processing','candidate',"
            "'pending','approved','rejected','published','unpublished')",
            name="ck_article_status",
        ),
        Index("ix_article_status_level", "status", "source_level"),
        Index("ix_article_url_hash", "url_hash"),
    )
