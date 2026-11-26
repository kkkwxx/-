"""任务运行记录。`task_run` 是唯一任务真相（04-采集 §1.3）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, utcnow

TASK_TYPES = ("poll", "fetch_full_text", "recalc_levels")
TASK_STATUSES = ("pending", "running", "success", "failed")
TRIGGERED_BY = ("scheduler", "manual", "startup")


class TaskRun(Base, TimestampMixin):
    __tablename__ = "task_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(24), nullable=False, default="poll")
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("source.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    triggered_by: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduler"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "task_type IN ('poll','fetch_full_text','recalc_levels')",
            name="ck_task_type",
        ),
        CheckConstraint(
            "status IN ('pending','running','success','failed')", name="ck_task_status"
        ),
        CheckConstraint(
            "triggered_by IN ('scheduler','manual','startup')", name="ck_task_triggered_by"
        ),
        # tick 的 NOT EXISTS 子查询靠这个索引
        Index("ix_task_run_source_status", "source_id", "status"),
        Index("ix_task_run_status_type", "status", "task_type"),
    )
