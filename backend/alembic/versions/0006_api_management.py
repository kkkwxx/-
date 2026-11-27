"""api_management: add ai_provider_config table and source encrypted api key columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_config",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "vendor",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'custom'"),
        ),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_mask", sa.String(length=64), nullable=True),
        sa.Column("model_classify", sa.String(length=64), nullable=False),
        sa.Column("model_generate", sa.String(length=64), nullable=False),
        sa.Column(
            "daily_token_budget",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("50000"),
        ),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'standby'"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_test_status", sa.String(length=16), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "vendor IN ('dashscope','deepseek','openai','siliconflow','gemini','ollama','custom')",
            name="ck_ai_provider_vendor",
        ),
        sa.CheckConstraint(
            "role IN ('primary','fallback','standby')",
            name="ck_ai_provider_role",
        ),
        sa.CheckConstraint(
            "daily_token_budget >= 0",
            name="ck_ai_provider_budget_nonneg",
        ),
    )
    op.create_index(
        "ix_ai_provider_role_enabled",
        "ai_provider_config",
        ["role", "enabled"],
        unique=False,
    )

    op.add_column("source", sa.Column("api_key_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "source", sa.Column("api_key_mask", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source", "api_key_mask")
    op.drop_column("source", "api_key_encrypted")
    op.drop_index("ix_ai_provider_role_enabled", table_name="ai_provider_config")
    op.drop_table("ai_provider_config")
