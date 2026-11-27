"""事件簇、不可变发布快照与发布流水记录。

口径：01-总体架构 §6、需求文档 v1.0 §6 / §8 / §9 / §11。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, utcnow

PUSH_ACTIONS = ("publish", "unpublish", "republish")
PUSH_STATUSES = ("success", "failed")


class EventCluster(Base, TimestampMixin):
    """跨源事件簇：把描述同一真实事件的多条不同媒体报道归并到一起。"""

    __tablename__ = "event_cluster"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_entity: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    event_action: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    event_date: Mapped[date | None] = mapped_column(Date)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lead_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("article.id", ondelete="SET NULL", use_alter=True, name="fk_event_cluster_lead_article")
    )
    article_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_event_cluster_type_date", "event_type", "event_date"),
        Index("ix_event_cluster_first_seen", "first_seen_at"),
    )


class PublishedArticle(Base, TimestampMixin):
    """不可变发布快照：栏目展示页只读此表，避免审核后编辑污染线上展示。"""

    __tablename__ = "published_article"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_cluster.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    title_final: Mapped[str] = mapped_column(Text, nullable=False)
    body_lead: Mapped[str] = mapped_column(Text, nullable=False)
    body_summary: Mapped[str] = mapped_column(Text, nullable=False)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_level: Mapped[str] = mapped_column(String(1), nullable=False)

    content_nature: Mapped[str | None] = mapped_column(String(32))
    disclaimer: Mapped[str | None] = mapped_column(Text)
    style_tag: Mapped[str | None] = mapped_column(String(32))
    hook_score: Mapped[int | None] = mapped_column(SmallInteger)
    culture_tag: Mapped[str | None] = mapped_column(String(32))
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    unpublished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator: Mapped[str] = mapped_column(
        String(64), nullable=False, default="admin", server_default=text("'admin'")
    )

    __table_args__ = (
        CheckConstraint("source_level IN ('A','B','C')", name="ck_published_source_level"),
        Index("ix_published_active_time", "is_active", "published_at"),
        Index("ix_published_event_type", "event_type", "is_active"),
    )


class PushRecord(Base):
    """内部发布 / 下架 / 重发流水记录。"""

    __tablename__ = "push_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    published_article_id: Mapped[int] = mapped_column(
        ForeignKey("published_article.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="publish", server_default=text("'publish'")
    )
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal_column", server_default=text("'internal_column'")
    )
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="success", server_default=text("'success'")
    )
    response: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(
        String(64), nullable=False, default="admin", server_default=text("'admin'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('publish','unpublish','republish')", name="ck_push_record_action"
        ),
        CheckConstraint(
            "status IN ('success','failed')", name="ck_push_record_status"
        ),
    )
