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
            base.etag = outcome.headers.get("etag") or etag
            base.last_modified = outcome.headers.get("last-modified") or last_modified
            base.stats = {"fetched": 0, "not_modified": 1}
            return base

        base.etag = outcome.headers.get("etag")
        base.last_modified = outcome.headers.get("last-modified")
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
    """API 适配器：支持 NASA JPL CNEOS 小行星飞掠 API；其余未配置 Key 的 API 返回 auth。"""

    # 1 AU ≈ 389.17 地月距离 (Lunar Distance)
    _AU_TO_LD = 389.17

    def fetch(
        self,
        *,
        url: str,
        source_id: int,
        timeout: float | None = None,
        max_attempts: int | None = None,
        **_: Any,
    ) -> FetchResult:
        if "ssd-api.jpl.nasa.gov/cad.api" in (url or ""):
            return self._fetch_jpl_cad(
                url=url, source_id=source_id, timeout=timeout, max_attempts=max_attempts
            )
        return FetchResult(
            items=[],
            error_kind="auth",
            error="ApiAdapter 本期未实现：该源需要 API Key（决策 C3）",
            stats={"reason": "api_adapter_not_implemented"},
        )

    def _fetch_jpl_cad(
        self,
        *,
        url: str,
        source_id: int,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> FetchResult:
        import json
        from urllib.parse import quote

        outcome = fetch(url, timeout=timeout, max_attempts=max_attempts)
        base = FetchResult(attempts=outcome.attempts, duration_ms=outcome.duration_ms)
        if outcome.error_kind:
            base.error_kind = outcome.error_kind
            base.error = outcome.error
            return base

        try:
            payload = json.loads(outcome.content.decode("utf-8", errors="replace"))
        except ValueError as exc:
            base.error_kind = "parse"
            base.error = f"JPL CAD JSON 解析失败：{exc}"
            return base

        fields = payload.get("fields") or []
        rows = payload.get("data") or []
        base.stats = {"fetched": len(rows), "http_status": outcome.status_code, "api": "jpl_cad"}
        if not rows:
            base.error_kind = "empty_feed"
            base.error = "JPL CAD 返回 0 条近地天体记录"
            return base

        idx = {name: i for i, name in enumerate(fields)}
        now = datetime.now(timezone.utc)

        for row in rows:
            try:
                des = str(row[idx["des"]]).strip()
                cd = str(row[idx["cd"]]).strip()
                dist_au = float(row[idx["dist"]])
                v_rel = float(row[idx["v_rel"]]) if "v_rel" in idx and row[idx["v_rel"]] else None
                h_mag = float(row[idx["h"]]) if "h" in idx and row[idx["h"]] else None
            except (KeyError, IndexError, TypeError, ValueError):
                continue

            dist_ld = dist_au * self._AU_TO_LD
            dist_km = dist_au * 149_597_870.7
            # 用绝对星等 H 粗估直径（反照率 0.14 常规估算）
            diam_text = ""
            if h_mag is not None:
                diam_m = int(round(1329.0 / (0.14 ** 0.5) * (10 ** (-0.2 * h_mag)) * 1000))
                diam_text = f"，估算直径约 {diam_m} 米"

            # 解析 cd（如 "2026-Nov-29 14:22"）作为 event_time 线索
            cd_iso = cd
            try:
                cd_dt = date_parser.parse(cd).replace(tzinfo=timezone.utc)
                cd_iso = cd_dt.strftime("%Y-%m-%d %H:%M UTC")
            except (ValueError, OverflowError):
                pass

            lunar_note = "比月球还近！" if dist_ld < 1.0 else f"约 {dist_ld:.2f} 倍地月距离"
            title = f"近地小行星 {des} 将于 {cd_iso[:10]} 近距离飞掠地球（{lunar_note}）"
            speed_text = f"，相对速度约 {v_rel:.1f} 公里/秒" if v_rel is not None else ""
            summary = (
                f"NASA JPL 近地天体研究中心（CNEOS）监测显示，近地小行星 {des} 预计于 {cd_iso} "
                f"近距离飞掠地球，最近距离约 {dist_ld:.2f} 倍地月距离（约 {dist_km / 10000:.1f} 万公里）"
                f"{diam_text}{speed_text}。目前观测轨道表明无撞击地球威胁。"
            )
            detail_url = f"https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={quote(des)}&cd={quote(cd[:11])}"
            base.items.append(
                RawItemDraft(
                    source_id=source_id,
                    url=detail_url,
                    resolved_url=detail_url,
                    publisher_url="https://cneos.jpl.nasa.gov",
                    publisher_name="NASA JPL CNEOS",
                    title=title,
                    summary=summary,
                    content_encoded=summary,
                    published_at=now,
                    published_raw=cd,
                    fetched_at=now,
                    extra={
                        "jpl_des": des,
                        "jpl_cd": cd_iso,
                        "dist_ld": round(dist_ld, 3),
                        "is_future_event": True,
                    },
                )
            )
        return base


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
