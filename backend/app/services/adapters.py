"""适配器：FeedAdapter（rss + google_news）+ ApiAdapter 桩。解析规则见 04 §4 / §8。"""

from __future__ import annotations

import calendar
import html
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

import feedparser
from dateutil import parser as date_parser

from app.services.dto import FetchResult, RawItemDraft
from app.services.fetch import fetch

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _decode(raw: bytes, declared: str | None) -> tuple[str, str]:
    """先按 UTF-8 严格解，再嗅探（04 §4.2 ①）。"""
    try:
        return raw.decode("utf-8", errors="strict"), "utf-8-strict"
    except UnicodeDecodeError:
        pass
    if declared and declared.lower() not in ("utf-8", "utf8"):
        try:
            return raw.decode(declared, errors="strict"), f"declared:{declared}"
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def clean_text(value: str | None) -> str:
    """去 HTML 标签（GN 的 description 是转义 HTML，先 unescape）+ 合并空白 + NFKC。"""
    if not value:
        return ""
    text = html.unescape(value)
    text = _TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", text).strip()


def strip_title_suffix(title: str, rules: list[str] | tuple[str, ...]) -> str:
    result = title.strip()
    for rule in rules or ():
        if rule and result.endswith(rule):
            result = result[: -len(rule)].strip()
    return result


def parse_published(entry: dict[str, Any]) -> tuple[datetime | None, str | None, dict[str, Any]]:
    """① published_parsed 直取 ② dateutil 兜底 ③ 失败留空（04 §8.3）。"""
    extra: dict[str, Any] = {}
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    raw = entry.get("published") or entry.get("updated")
    if struct:
        return datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc), raw, extra
    if raw:
        try:
            parsed = date_parser.parse(raw, dayfirst=False)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
                extra["tz_assumed"] = "UTC"
            return parsed.astimezone(timezone.utc), raw, extra
        except (ValueError, OverflowError):
            extra["time_parse_failed"] = True
            return None, raw, extra
    extra["time_parse_failed"] = True
    return None, raw, extra


def _content_encoded(entry: dict[str, Any]) -> str | None:
    """RSS 自带全文（NASA 约 23 KB/条）。有就不抓详情页。"""
    for block in entry.get("content") or []:
        value = block.get("value")
        if value and len(value) > 0:
            return value
    return None


class FeedAdapter:
    """rss 与 google_news 共用一套解析，差别只在取出处域名的字段（见 grading.origin_domain）。"""

    def fetch(
        self,
        *,
        url: str,
        source_id: int,
        source_type: str,
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
        title_suffix_rules: list[str] | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> FetchResult:
        headers = dict(extra_headers or {})
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        outcome = fetch(
            url, extra_headers=headers, timeout=timeout, max_attempts=max_attempts
        )
        base = FetchResult(attempts=outcome.attempts, duration_ms=outcome.duration_ms)
        if outcome.error_kind:
            base.error_kind = outcome.error_kind
            base.error = outcome.error
            return base
        if outcome.not_modified:
            base.not_modified = True
            base.stats = {"fetched": 0, "not_modified": 1}
            return base

        text, strategy = _decode(outcome.content, outcome.headers.get("content-type"))
        parsed = feedparser.parse(text)
        entries = parsed.get("entries") or []
        bozo = 1 if parsed.get("bozo") else 0
        base.stats = {
            "fetched": len(entries),
            "bozo": bozo,
            "decode": strategy,
            "attempts": outcome.attempts,
            "http_status": outcome.status_code,
        }
        if bozo:
            exc = parsed.get("bozo_exception")
            base.stats["bozo_exception"] = str(exc)[:500]

        if not entries:
            # 四象限：bozo=1 → parse（真坏）；bozo=0 → empty_feed（空壳，不算失败）
            if bozo:
                base.error_kind = "parse"
                base.error = f"返回内容不是合法 feed：{parsed.get('bozo_exception')}"
            else:
                base.error_kind = "empty_feed"
                base.error = "返回 0 条。feed 可能是空壳页，或该源近期确实无更新"
            return base

        fetched_at = datetime.now(timezone.utc)
        suffix_rules = list(title_suffix_rules or [])
        for entry in entries:
            draft = self._to_draft(
                entry, source_id, source_type, fetched_at, suffix_rules
            )
            if draft is not None:
                base.items.append(draft)
        return base

    def _to_draft(
        self,
        entry: dict[str, Any],
        source_id: int,
        source_type: str,
        fetched_at: datetime,
        suffix_rules: list[str],
    ) -> RawItemDraft | None:
        link = (entry.get("link") or "").strip()
        raw_title = (entry.get("title") or "").strip()
        if not link or not raw_title:
            return None
        summary = clean_text(entry.get("summary") or entry.get("description"))
        published_at, published_raw, extra = parse_published(entry)
        extra["decode_title_cleaned"] = strip_title_suffix(raw_title, suffix_rules)

        publisher_name = publisher_url = None
        resolved_url = None
        if source_type == "google_news":
            src = entry.get("source") or {}
            publisher_name = src.get("title")
            publisher_url = src.get("href")
            resolved_url = None
        else:
            resolved_url = link

        content = _content_encoded(entry)
        if content:
            extra["has_content_encoded"] = len(content)
        author = entry.get("author")

        return RawItemDraft(
            source_id=source_id,
            url=link,
            title=raw_title,
            fetched_at=fetched_at,
            resolved_url=resolved_url,
            publisher_url=publisher_url,
            publisher_name=publisher_name,
            summary=summary or None,
            content_encoded=content,
            published_at=published_at,
            published_raw=published_raw,
            author=author[:255] if author else None,
            extra=extra,
        )


class ApiAdapter:
    """本期只留桩（决策 C3）：tianapi 需 key 且原地址 404，写了也无法验证。"""

    def fetch(self, *, url: str, source_id: int, **_: Any) -> FetchResult:
        return FetchResult(
            items=[],
            error_kind="auth",
            error="ApiAdapter 本期未实现：该源需要 API Key（决策 C3）",
            stats={"reason": "api_adapter_not_implemented"},
        )


_ADAPTERS = {"rss": FeedAdapter(), "google_news": FeedAdapter(), "api": ApiAdapter()}


def get_adapter(source_type: str) -> FeedAdapter | ApiAdapter | None:
    return _ADAPTERS.get(source_type)


def build_google_news_url(query: str) -> str:
    """04 §5.3 基础模板。query 存 query_template，url 存拼好的完整地址。"""
    from urllib.parse import quote

    return (
        "https://news.google.com/rss/search?q="
        f"{quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
