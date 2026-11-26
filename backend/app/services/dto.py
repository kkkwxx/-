"""适配器与定级之间的中间结构（04 §1.5 / §8.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class RawItemDraft:
    source_id: int
    url: str
    title: str
    fetched_at: datetime
    resolved_url: str | None = None
    publisher_url: str | None = None
    publisher_name: str | None = None
    summary: str | None = None
    content_encoded: str | None = None
    published_at: datetime | None = None
    published_raw: str | None = None
    author: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FetchResult:
    items: list[RawItemDraft] = field(default_factory=list)
    not_modified: bool = False
    error_kind: str | None = None
    error: str | None = None
    attempts: int = 0
    duration_ms: int = 0
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceConfig:
    """定级只需要源的类型 / 等级 / 期望域名，避免把 ORM 对象传进纯函数。"""

    id: int
    code: str
    type: str
    level: str
    expected_domains: tuple[str, ...] = ()
