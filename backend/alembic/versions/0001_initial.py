"""initial schema: 7 张业务表 + pg_trgm

手写迁移，逐列对齐 app/models/*（列名 / 类型 / 约束名 / 索引名完全一致），
目的是让 `alembic check` 与 `alembic revision --autogenerate` 都产出空迁移。

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 技术栈 §5.3：迁移里建扩展，标题模糊检索与相似度去重都靠它
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------------- source ----------------
    op.create_table(
        "source",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=1), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("query_template", sa.Text(), nullable=True),
        sa.Column("poll_interval_min", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "expected_domains",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "title_suffix_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "extra_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("api_key_env", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.String(length=256), nullable=True),
        sa.Column("last_modified", sa.String(length=128), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("health_status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_kind", sa.String(length=16), nullable=True),
        sa.Column("effective_ratio_7d", sa.Float(), nullable=True),
        sa.Column("expected_status_on_first_run", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('rss','google_news','api','manual')", name="ck_source_type"
        ),
        sa.CheckConstraint("level IN ('A','B','C')", name="ck_source_level"),
        sa.CheckConstraint(
            "health_status IN ('ok','degraded','down','auth_error','starved','disabled')",
            name="ck_source_health",
        ),
        sa.CheckConstraint(
            "last_error_kind IS NULL OR last_error_kind IN "
            "('connect','http_4xx','http_5xx','auth','parse','empty_feed')",
            name="ck_source_error_kind",
        ),
    )

    # ---------------- source_domain_rule ----------------
    op.create_table(
        "source_domain_rule",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("level", sa.String(length=1), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("level IN ('A','B','C')", name="ck_domain_rule_level"),
    )

    # ---------------- unknown_domain ----------------
    op.create_table(
        "unknown_domain",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_title", sa.Text(), nullable=True),
        sa.Column("sample_source_code", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("domain", name="uq_unknown_domain_domain"),
    )

    # ---------------- task_run ----------------
    op.create_table(
        "task_run",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("task_type", sa.String(length=24), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("triggered_by", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stats_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name="task_run_source_id_fkey",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "task_type IN ('poll','fetch_full_text','recalc_levels')",
            name="ck_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','success','failed')", name="ck_task_status"
        ),
        sa.CheckConstraint(
            "triggered_by IN ('scheduler','manual','startup')",
            name="ck_task_triggered_by",
        ),
    )

    # ---------------- raw_item ----------------
    op.create_table(
        "raw_item",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("resolved_url", sa.Text(), nullable=True),
        sa.Column("publisher_url", sa.Text(), nullable=True),
        sa.Column("publisher_name", sa.String(length=255), nullable=True),
        sa.Column("raw_title", sa.Text(), nullable=False),
        sa.Column("raw_summary", sa.Text(), nullable=True),
        sa.Column("content_encoded", sa.Text(), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column("full_text_status", sa.String(length=16), nullable=False),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_raw", sa.String(length=128), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name="raw_item_source_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("source_id", "url_hash", name="uq_raw_item_source_url"),
        sa.CheckConstraint(
            "full_text_status IN ('pending','ok','blocked','thin','skipped')",
            name="ck_raw_item_ft_status",
        ),
    )

    # ---------------- article ----------------
    op.create_table(
        "article",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("publisher_name", sa.String(length=255), nullable=True),
        sa.Column("publisher_url", sa.Text(), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("source_level", sa.String(length=1), nullable=False),
        sa.Column("source_level_reason", sa.String(length=255), nullable=False),
        sa.Column("resolve_status", sa.String(length=16), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("title_fingerprint", sa.Text(), nullable=True),
        sa.Column("dup_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_event", sa.Boolean(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_nature", sa.String(length=32), nullable=True),
        sa.Column("risk_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_item_id"],
            ["raw_item.id"],
            name="article_raw_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("source_level IN ('A','B','C')", name="ck_article_level"),
        sa.CheckConstraint(
            "resolve_status IN ('resolved','unresolved')", name="ck_article_resolve"
        ),
        sa.CheckConstraint(
            "status IN ('ingested','rule_rejected','duplicate','processing','candidate',"
            "'pending','approved','rejected','published','unpublished')",
            name="ck_article_status",
        ),
    )

    # ---------------- review_action ----------------
    op.create_table(
        "review_action",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["article.id"],
            name="review_action_article_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name="review_action_source_id_fkey",
            ondelete="SET NULL",
        ),
    )

    # ---------------- 索引（名字与 models 的 Index / index=True 完全一致）----------------
    op.create_index("ix_source_health_enabled", "source", ["health_status", "enabled"])
    op.create_index("ix_unknown_domain_hit", "unknown_domain", ["hit_count"])
    op.create_index("ix_task_run_source_id", "task_run", ["source_id"])
    op.create_index(
        "ix_task_run_source_status", "task_run", ["source_id", "status"]
    )
    op.create_index("ix_task_run_status_type", "task_run", ["status", "task_type"])
    op.create_index("ix_raw_item_source_id", "raw_item", ["source_id"])
    op.create_index(
        "ix_raw_item_ft_status", "raw_item", ["full_text_status", "fetched_at"]
    )
    op.create_index("ix_raw_item_url_hash", "raw_item", ["url_hash"])
    op.create_index(
        "ix_article_status_level", "article", ["status", "source_level"]
    )
    op.create_index("ix_article_url_hash", "article", ["url_hash"])
    op.create_index("ix_review_action_article_id", "review_action", ["article_id"])
    op.create_index("ix_review_action_source_id", "review_action", ["source_id"])
    op.create_index(
        "ix_review_action_action_time", "review_action", ["action", "created_at"]
    )

    # 技术栈 §5.3：标题 trigram GIN 索引（后台关键词搜索 + 相似度去重）
    op.create_index(
        "ix_article_title_trgm",
        "article",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # 反序：先索引，再表
    op.drop_index("ix_article_title_trgm", table_name="article")
    op.drop_index("ix_review_action_action_time", table_name="review_action")
    op.drop_index("ix_review_action_source_id", table_name="review_action")
    op.drop_index("ix_review_action_article_id", table_name="review_action")
    op.drop_index("ix_article_url_hash", table_name="article")
    op.drop_index("ix_article_status_level", table_name="article")
    op.drop_index("ix_raw_item_url_hash", table_name="raw_item")
    op.drop_index("ix_raw_item_ft_status", table_name="raw_item")
    op.drop_index("ix_raw_item_source_id", table_name="raw_item")
    op.drop_index("ix_task_run_status_type", table_name="task_run")
    op.drop_index("ix_task_run_source_status", table_name="task_run")
    op.drop_index("ix_task_run_source_id", table_name="task_run")
    op.drop_index("ix_unknown_domain_hit", table_name="unknown_domain")
    op.drop_index("ix_source_health_enabled", table_name="source")

    op.drop_table("review_action")
    op.drop_table("article")
    op.drop_table("raw_item")
    op.drop_table("task_run")
    op.drop_table("unknown_domain")
    op.drop_table("source_domain_rule")
    op.drop_table("source")

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
