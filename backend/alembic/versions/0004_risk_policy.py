"""risk_policy: add risk_policy_config table for dynamic risk profile & thresholds

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-27 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_policy_config",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "profile",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'balanced'"),
        ),
        sa.Column(
            "overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_by",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "profile IN ('strict','balanced','relaxed')",
            name="ck_risk_policy_profile",
        ),
    )


def downgrade() -> None:
    op.drop_table("risk_policy_config")
