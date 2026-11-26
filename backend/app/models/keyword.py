"""关键词规则（01-总体架构 §6 已声明的 ``keyword_rule`` 实体；口径见 05 §3）。

* ``type``：``exclude_hard`` / ``exclude_soft`` / ``anchor`` / ``signal`` / ``event_verb``
* ``pattern``：**正则**，一律 ``re.search`` + ``re.I``；纯词也写成正则字面量
* ``weight``：``signal`` 加分、``exclude_soft`` 扣分；其余类型忽略
* ``scope``：排除词一律 ``title_summary``，不扫正文（避免正文引用误伤，05 §3）

唯一约束 ``(type, category, pattern)`` 让 ``python -m app.seed`` 可以按业务键幂等 upsert。
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

RULE_TYPES = ("exclude_hard", "exclude_soft", "anchor", "signal", "event_verb")
RULE_SCOPES = ("title", "title_summary", "all")


class KeywordRule(Base, TimestampMixin):
    __tablename__ = "keyword_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="title_summary"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "type", "category", "pattern", name="uq_keyword_rule_type_cat_pattern"
        ),
        CheckConstraint(
            "type IN ('exclude_hard','exclude_soft','anchor','signal','event_verb')",
            name="ck_keyword_rule_type",
        ),
        CheckConstraint(
            "scope IN ('title','title_summary','all')", name="ck_keyword_rule_scope"
        ),
    )
