"""AI API 提供商配置表（支持多提供商、主备自动降级、独立模型与日预算、加密存 Key）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

AI_PROVIDER_VENDORS = (
    "dashscope",
    "deepseek",
    "openai",
    "siliconflow",
    "gemini",
    "ollama",
    "custom",
)
AI_PROVIDER_ROLES = ("primary", "fallback", "standby")


class AiProviderConfig(Base, TimestampMixin):
    """AI 模型提供商配置（OpenAI 兼容接口）。"""

    __tablename__ = "ai_provider_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor: Mapped[str] = mapped_column(
        String(32), nullable=False, default="custom", server_default=text("'custom'")
    )
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_mask: Mapped[str | None] = mapped_column(String(64))

    model_classify: Mapped[str] = mapped_column(String(64), nullable=False)
    model_generate: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_token_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50_000, server_default=text("50000")
    )

    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="standby", server_default=text("'standby'")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    note: Mapped[str | None] = mapped_column(Text)

    last_test_status: Mapped[str | None] = mapped_column(String(16))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "vendor IN ('dashscope','deepseek','openai','siliconflow','gemini','ollama','custom')",
            name="ck_ai_provider_vendor",
        ),
        CheckConstraint(
            "role IN ('primary','fallback','standby')",
            name="ck_ai_provider_role",
        ),
        CheckConstraint(
            "daily_token_budget >= 0",
            name="ck_ai_provider_budget_nonneg",
        ),
        Index("ix_ai_provider_role_enabled", "role", "enabled"),
    )
