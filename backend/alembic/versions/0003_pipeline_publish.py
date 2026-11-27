"""pipeline_publish: event_cluster + published_article + push_record + article 06/07 fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARTICLE_STATUSES_OLD = (
    "'ingested','rule_rejected','duplicate','processing','candidate',"
    "'pending','approved','rejected','published','unpublished'"
)
_ARTICLE_STATUSES_NEW = (
    "'ingested','rule_rejected','duplicate','processing','judged','expired',"
    "'candidate','pending','approved','rejected','published','unpublished'"
)
_TASK_TYPES_OLD = "'poll','fetch_full_text','recalc_levels','preprocess'"
_TASK_TYPES_NEW = "'poll','fetch_full_text','recalc_levels','preprocess','generate_content'"


def upgrade() -> None:
    # ---------------- event_cluster ----------------
    op.create_table(
        "event_cluster",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("primary_entity", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("event_action", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("canonical_title", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_article_id", sa.Integer(), nullable=True),
        sa.Column("article_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_cluster_type_date", "event_cluster", ["event_type", "event_date"])
    op.create_index("ix_event_cluster_first_seen", "event_cluster", ["first_seen_at"])

    # ---------------- article 06/07 字段 ----------------
    op.add_column("article", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "article",
        sa.Column("is_future_event", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("article", sa.Column("event_cluster_id", sa.Integer(), nullable=True))
    op.add_column(
        "article",
        sa.Column(
            "entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("article", sa.Column("event_action", sa.String(length=64), nullable=True))
    op.add_column("article", sa.Column("fact_anchor", sa.Text(), nullable=True))
    op.add_column(
        "article",
        sa.Column(
            "title_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("article", sa.Column("title_final", sa.Text(), nullable=True))
    op.add_column("article", sa.Column("body_lead", sa.Text(), nullable=True))
    op.add_column("article", sa.Column("body_summary", sa.Text(), nullable=True))
    op.add_column("article", sa.Column("hook_score", sa.SmallInteger(), nullable=True))
    op.add_column("article", sa.Column("style_tag", sa.String(length=32), nullable=True))
    op.add_column(
        "article",
        sa.Column("need_review", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column("article", sa.Column("culture_tag", sa.String(length=32), nullable=True))
    op.add_column(
        "article",
        sa.Column(
            "evidence_notes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("article", sa.Column("ai_generated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "article_event_cluster_id_fkey",
        "article",
        "event_cluster",
        ["event_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_event_cluster_lead_article",
        "event_cluster",
        "article",
        ["lead_article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_article_event_cluster_id", "article", ["event_cluster_id"])
    op.create_index("ix_article_title_fp", "article", ["title_fingerprint"])

    # ---------------- 更新状态约束与任务类型约束 ----------------
    op.drop_constraint("ck_article_status", "article", type_="check")
    op.create_check_constraint(
        "ck_article_status", "article", f"status IN ({_ARTICLE_STATUSES_NEW})"
    )
    op.drop_constraint("ck_task_type", "task_run", type_="check")
    op.create_check_constraint(
        "ck_task_type", "task_run", f"task_type IN ({_TASK_TYPES_NEW})"
    )

    # ---------------- published_article ----------------
    op.create_table(
        "published_article",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("event_cluster_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title_final", sa.Text(), nullable=False),
        sa.Column("body_lead", sa.Text(), nullable=False),
        sa.Column("body_summary", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_level", sa.String(length=1), nullable=False),
        sa.Column("content_nature", sa.String(length=32), nullable=True),
        sa.Column("disclaimer", sa.Text(), nullable=True),
        sa.Column("style_tag", sa.String(length=32), nullable=True),
        sa.Column("hook_score", sa.SmallInteger(), nullable=True),
        sa.Column("culture_tag", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unpublished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"], ["article.id"], name="published_article_article_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["event_cluster_id"],
            ["event_cluster.id"],
            name="published_article_event_cluster_id_fkey",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("source_level IN ('A','B','C')", name="ck_published_source_level"),
    )
    op.create_index("ix_published_article_article_id", "published_article", ["article_id"])
    op.create_index(
        "ix_published_article_event_cluster_id", "published_article", ["event_cluster_id"]
    )
    op.create_index("ix_published_active_time", "published_article", ["is_active", "published_at"])
    op.create_index("ix_published_event_type", "published_article", ["event_type", "is_active"])

    # ---------------- push_record ----------------
    op.create_table(
        "push_record",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("published_article_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="publish"),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="internal_column"),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="success"),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["published_article_id"],
            ["published_article.id"],
            name="push_record_published_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "action IN ('publish','unpublish','republish')", name="ck_push_record_action"
        ),
        sa.CheckConstraint("status IN ('success','failed')", name="ck_push_record_status"),
    )
    op.create_index(
        "ix_push_record_published_article_id", "push_record", ["published_article_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_push_record_published_article_id", table_name="push_record")
    op.drop_table("push_record")

    op.drop_index("ix_published_event_type", table_name="published_article")
    op.drop_index("ix_published_active_time", table_name="published_article")
    op.drop_index("ix_published_article_event_cluster_id", table_name="published_article")
    op.drop_index("ix_published_article_article_id", table_name="published_article")
    op.drop_table("published_article")

    op.execute("DELETE FROM task_run WHERE task_type = 'generate_content'")
    op.drop_constraint("ck_task_type", "task_run", type_="check")
    op.create_check_constraint("ck_task_type", "task_run", f"task_type IN ({_TASK_TYPES_OLD})")

    op.execute("UPDATE article SET status = 'pending' WHERE status IN ('judged','expired')")
    op.drop_constraint("ck_article_status", "article", type_="check")
    op.create_check_constraint(
        "ck_article_status", "article", f"status IN ({_ARTICLE_STATUSES_OLD})"
    )

    op.drop_index("ix_article_title_fp", table_name="article")
    op.drop_index("ix_article_event_cluster_id", table_name="article")
    op.drop_constraint("fk_event_cluster_lead_article", "event_cluster", type_="foreignkey")
    op.drop_constraint("article_event_cluster_id_fkey", "article", type_="foreignkey")

    for col in (
        "ai_generated_at",
        "evidence_notes",
        "culture_tag",
        "need_review",
        "style_tag",
        "hook_score",
        "body_summary",
        "body_lead",
        "title_final",
        "title_candidates",
        "fact_anchor",
        "event_action",
        "entities",
        "event_cluster_id",
        "is_future_event",
        "first_seen_at",
    ):
        op.drop_column("article", col)

    op.drop_index("ix_event_cluster_first_seen", table_name="event_cluster")
    op.drop_index("ix_event_cluster_type_date", table_name="event_cluster")
    op.drop_table("event_cluster")
