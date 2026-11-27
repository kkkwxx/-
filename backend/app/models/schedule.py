"""采集与 AI 调度策略配置表（全局单行配置，默认 manual 纯手动保护额度）。"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

SCHEDULE_MODES = ("manual", "window", "fixed_times")


class PipelineScheduleConfig(Base, TimestampMixin):
    """全局采集与 AI 自动调度策略（单行生效）。"""

    __tablename__ = "pipeline_schedule_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=text("'manual'")
    )
    crawl_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ai_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Asia/Shanghai",
        server_default=text("'Asia/Shanghai'"),
    )
    # 1=周一 ... 7=周日
    weekdays: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: [1, 2, 3, 4, 5, 6, 7],
        server_default=text("'[1, 2, 3, 4, 5, 6, 7]'::jsonb"),
    )
    window_start: Mapped[str] = mapped_column(
        String(5), nullable=False, default="09:00", server_default=text("'09:00'")
    )
    window_end: Mapped[str] = mapped_column(
        String(5), nullable=False, default="22:00", server_default=text("'22:00'")
    )
    fixed_times: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: ["09:00", "14:00", "20:00"],
        server_default=text("'[\"09:00\", \"14:00\", \"20:00\"]'::jsonb"),
    )
    max_ai_items_per_run: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    last_fixed_slot_key: Mapped[str | None] = mapped_column(String(32))
    updated_by: Mapped[str] = mapped_column(
        String(64), nullable=False, default="system", server_default=text("'system'")
    )

    __table_args__ = (
        CheckConstraint(
            "mode IN ('manual','window','fixed_times')",
            name="ck_pipeline_schedule_mode",
        ),
        CheckConstraint(
            "max_ai_items_per_run >= 1 AND max_ai_items_per_run <= 100",
            name="ck_pipeline_schedule_max_items",
        ),
    )
