"""适配器与抓取层：bozo 四象限、GN <source url>、304、auth 不重试、5xx 重试（04 §4.2 / §14.3）。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.adapters import FeedAdapter

FEEDS = Path(__file__).parent / "fixtures" / "feeds"
URL = "https://example.com/feed/"


def _feed(name: str) -> bytes:
    return (FEEDS / name).read_bytes()


def _adapter(source_type: str = "rss") -> FeedAdapter:
    return FeedAdapter()


@respx.mock
def test_parse_nasa_feed():
    respx.get(URL).mock(return_value=httpx.Response(200, content=_feed("nasa.xml")))
    result = _adapter().fetch(url=URL, source_id=1, source_type="rss",
                              title_suffix_rules=["- NASA"])
    assert result.error_kind is None
    assert len(result.items) == 3
    assert result.stats["bozo"] == 0

    first = result.items[0]
    assert first.content_encoded and "<p>" in first.content_encoded
    assert "<p>" not in (first.summary or "")
    assert first.published_at is not None and first.published_at.tzinfo is not None
    assert first.extra["decode_title_cleaned"] == "NASA 发现一颗近地小行星将于 2027 年飞掠地球"
    assert first.resolved_url == first.url
    assert first.publisher_url is None


@respx.mock
def test_parse_google_news_feed_extracts_source_url():
    respx.get(URL).mock(return_value=httpx.Response(200, content=_feed("google_news.xml")))
    result = _adapter().fetch(url=URL, source_id=2, source_type="google_news")
    assert len(result.items) == 3

    first, second, third = result.items
    assert first.publisher_url == "https://www.cast.org.cn"
    assert first.publisher_name == "中国科学技术协会"
    assert first.resolved_url is None  # GN 不回溯（04 §9.1）
    assert second.publisher_url == "https://www.xinhuanet.com"
    # 缺 <source> 的条目 → 定级会落 rule1
    assert third.publisher_url is None


@respx.mock
def test_empty_feed_is_not_parse_error():
    respx.get(URL).mock(return_value=httpx.Response(200, content=_feed("empty.xml")))
    result = _adapter().fetch(url=URL, source_id=3, source_type="rss")
    assert result.error_kind == "empty_feed"
    assert result.items == []


@respx.mock
def test_html_page_is_parse_error():
    respx.get(URL).mock(return_value=httpx.Response(200, content=_feed("html_not_feed.html")))
    result = _adapter().fetch(url=URL, source_id=4, source_type="rss")
    assert result.error_kind == "parse"
    assert result.items == []


@respx.mock
def test_304_not_modified():
    route = respx.get(URL).mock(return_value=httpx.Response(304))
    result = _adapter().fetch(url=URL, source_id=5, source_type="rss", etag='"abc"')
    assert result.not_modified is True
    assert result.error_kind is None
    assert route.calls[0].request.headers["If-None-Match"] == '"abc"'


@respx.mock
def test_403_is_auth_and_not_retried():
    route = respx.get(URL).mock(return_value=httpx.Response(403))
    result = _adapter().fetch(url=URL, source_id=6, source_type="rss")
    assert result.error_kind == "auth"
    assert route.call_count == 1  # auth 不重试（04 §13.1）


@respx.mock
def test_5xx_retries_until_attempts_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "CRAWL_MAX_ATTEMPTS", 2)
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    result = _adapter().fetch(url=URL, source_id=7, source_type="rss")
    assert result.error_kind == "http_5xx"
    assert result.attempts == 2
    assert route.call_count == 2


@respx.mock
def test_retry_recovers():
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, content=_feed("nasa.xml")),
    ]
    result = _adapter().fetch(url=URL, source_id=8, source_type="rss")
    assert result.error_kind is None
    assert result.attempts == 2
    assert len(result.items) == 3


@respx.mock
def test_connect_error_classified():
    respx.get(URL).mock(side_effect=httpx.ConnectError("SSL: UNEXPECTED_EOF_WHILE_READING"))
    result = _adapter().fetch(url=URL, source_id=9, source_type="rss", max_attempts=2)
    assert result.error_kind == "connect"
    assert result.attempts == 2


def test_api_adapter_is_stub():
    from app.services.adapters import ApiAdapter

    result = ApiAdapter().fetch(url="https://apis.tianapi.com/science/index", source_id=10)
    assert result.error_kind == "auth"
    assert result.items == []
    assert result.stats["reason"] == "api_adapter_not_implemented"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(404, "http_4xx"), (410, "http_4xx"), (500, "http_5xx")],
)
@respx.mock
def test_status_classification(status, kind, monkeypatch):
    monkeypatch.setattr(settings, "CRAWL_MAX_ATTEMPTS", 1)
    respx.get(URL).mock(return_value=httpx.Response(status))
    result = _adapter().fetch(url=URL, source_id=11, source_type="rss")
    assert result.error_kind == kind
