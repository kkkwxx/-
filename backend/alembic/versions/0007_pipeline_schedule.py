"""pipeline_schedule: add pipeline_schedule_config table and pipeline_run task_type

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_schedule_config",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "crawl_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "ai_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Asia/Shanghai'"),
        ),
        sa.Column(
            "weekdays",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[1, 2, 3, 4, 5, 6, 7]'::jsonb"),
        ),
        sa.Column(
            "window_start",
            sa.String(length=5),
            nullable=False,
            server_default=sa.text("'09:00'"),
        ),
        sa.Column(
            "window_end",
            sa.String(length=5),
            nullable=False,
            server_default=sa.text("'22:00'"),
        ),
        sa.Column(
            "fixed_times",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"09:00\", \"14:00\", \"20:00\"]'::jsonb"),
        ),
        sa.Column(
            "max_ai_items_per_run",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
        sa.Column("last_fixed_slot_key", sa.String(length=32), nullable=True),
        sa.Column(
            "updated_by",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('manual','window','fixed_times')",
            name="ck_pipeline_schedule_mode",
        ),
        sa.CheckConstraint(
            "max_ai_items_per_run >= 1 AND max_ai_items_per_run <= 100",
            name="ck_pipeline_schedule_max_items",
        ),
    )

    op.drop_constraint("ck_task_type", "task_run", type_="check")
    op.create_check_constraint(
        "ck_task_type",
        "task_run",
        "task_type IN ('poll','fetch_full_text','recalc_levels','preprocess','generate_content','pipeline_run')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_task_type", "task_run", type_="check")
    op.create_check_constraint(
        "ck_task_type",
        "task_run",
        "task_type IN ('poll','fetch_full_text','recalc_levels','preprocess','generate_content')",
    )
    op.drop_table("pipeline_schedule_config")
