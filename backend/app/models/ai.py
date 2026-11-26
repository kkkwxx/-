"""AI 调用留痕（01-总体架构 §6 的 ``ai_run``；口径见 05 §6.3）。

一行 = 一次模型调用（或一次回放命中）。作用有三：

1. **可追溯**：``input_hash = sha256(model + prompt_version + title + summary)``，
   同输入可直接复用结果，不重复付费；
2. **可核算**：``tokens`` / ``latency_ms`` 支撑 ``LLM_DAILY_TOKEN_BUDGET``；
3. **可排查**：失败也要落库（``status='failed'`` + ``error``），而不是只留日志。

``article_id`` 用 ``SET NULL``：文章被清理时留痕不能跟着消失（同 ``review_action``）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import utcnow

AI_RUN_STATUSES = ("success", "failed", "skipped")


class AiRun(Base):
    __tablename__ = "ai_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("article.id", ondelete="SET NULL")
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('success','failed','skipped')", name="ck_ai_run_status"
        ),
    )
