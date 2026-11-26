"""preprocess: keyword_rule + ai_run + article.event_confidence

口径：05-预处理与事件判定 §3（keyword_rule）/ §6.3（ai_run）/ §6.4（event_confidence）
      / §7.1（五类 + other）/ §9（task_run.task_type 新增 preprocess）。

手写迁移，逐列对齐 app/models/*（列名 / 类型 / 约束名 / 索引名完全一致），
目的是让 `alembic check` 与 `alembic revision --autogenerate` 都产出空迁移。

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26 00:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES = (
    "'deep_space_particle','neo','exoplanet','bci_consciousness','anomaly_psychic','other'"
)
_TASK_TYPES_OLD = "'poll','fetch_full_text','recalc_levels'"
_TASK_TYPES_NEW = "'poll','fetch_full_text','recalc_levels','preprocess'"


def upgrade() -> None:
    # ---------------- article.event_confidence ----------------
    op.add_column(
        "article", sa.Column("event_confidence", sa.Numeric(3, 2), nullable=True)
    )

    # ---------------- article.ck_article_event_type ----------------
    # 0001 没有这条约束，这里先 IF EXISTS 再建，重复执行/回滚后重跑都不会炸
    op.execute("ALTER TABLE article DROP CONSTRAINT IF EXISTS ck_article_event_type")
    op.create_check_constraint(
        "ck_article_event_type",
        "article",
        f"event_type IS NULL OR event_type IN ({_EVENT_TYPES})",
    )

    # ---------------- task_run.ck_task_type（加入 preprocess，05 §9）----------------
    op.drop_constraint("ck_task_type", "task_run", type_="check")
    op.create_check_constraint(
        "ck_task_type", "task_run", f"task_type IN ({_TASK_TYPES_NEW})"
    )

    # ---------------- keyword_rule（05 §3）----------------
    op.create_table(
        "keyword_rule",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "type", "category", "pattern", name="uq_keyword_rule_type_cat_pattern"
        ),
        sa.CheckConstraint(
            "type IN ('exclude_hard','exclude_soft','anchor','signal','event_verb')",
            name="ck_keyword_rule_type",
        ),
        sa.CheckConstraint(
            "scope IN ('title','title_summary','all')", name="ck_keyword_rule_scope"
        ),
    )

    # ---------------- ai_run（05 §6.3）----------------
    op.create_table(
        "ai_run",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["article.id"],
            name="ai_run_article_id_fkey",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('success','failed','skipped')", name="ck_ai_run_status"
        ),
    )
    op.create_index("ix_ai_run_input_hash", "ai_run", ["input_hash"])


def downgrade() -> None:
    # 反序：先索引与表，再约束与列
    op.drop_index("ix_ai_run_input_hash", table_name="ai_run")
    op.drop_table("ai_run")
    op.drop_table("keyword_rule")

    # 0001 的 ck_task_type 不认 'preprocess'：回退前必须先清掉这类任务记录，
    # 否则 ADD CONSTRAINT 直接违例（PG 的 DDL 在事务里，整条 downgrade 会回滚）
    op.execute("DELETE FROM task_run WHERE task_type = 'preprocess'")
    op.drop_constraint("ck_task_type", "task_run", type_="check")
    op.create_check_constraint(
        "ck_task_type", "task_run", f"task_type IN ({_TASK_TYPES_OLD})"
    )

    op.drop_constraint("ck_article_event_type", "article", type_="check")
    op.drop_column("article", "event_confidence")
