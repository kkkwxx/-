"""风控严厉度档位与动态阈值配置表（单行配置，id 固定为 1）。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

POLICY_PROFILES = ("strict", "balanced", "relaxed")


class RiskPolicyConfig(Base, TimestampMixin):
    """全局风控策略与阈值配置（单行表，支持运行时热切换无需重启服务）。"""

    __tablename__ = "risk_policy_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile: Mapped[str] = mapped_column(
        String(16), nullable=False, default="balanced", server_default=text("'balanced'")
    )
    overrides: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_by: Mapped[str] = mapped_column(
        String(64), nullable=False, default="system", server_default=text("'system'")
    )

    __table_args__ = (
        CheckConstraint(
            "profile IN ('strict','balanced','relaxed')",
            name="ck_risk_policy_profile",
        ),
    )
