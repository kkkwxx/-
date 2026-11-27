"""HTTP 抓取层：全局单 Client + tenacity 退避 + 同 host 互斥串行。

硬约束来源：技术栈 §5.5、04-采集 §7.1/§7.2、AI编码对照 §2.1。
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
AUTH_STATUS = frozenset({401, 403})

_client: httpx.Client | None = None
_client_lock = threading.Lock()

_host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_host_last: dict[str, float] = defaultdict(float)
_host_guard = threading.Lock()


class FetchError(Exception):
    def __init__(self, error_kind: str, message: str, attempts: int = 0) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message
        self.attempts = attempts


class _RetryableResponse(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


@dataclass(slots=True)
class FetchOutcome:
    status_code: int | None = None
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    not_modified: bool = False
    error_kind: str | None = None
    error: str | None = None
    attempts: int = 0
    duration_ms: int = 0
    truncated: bool = False


class _UnsafeRedirectError(httpx.HTTPError):
    """重定向目标命中 SSRF 防护拦截，禁止重试。"""


def _verify_request_hook(request: httpx.Request) -> None:
    """httpx 请求级钩子：拦截每一跳重定向指向内网/本机/非法协议的 SSRF 绕过。"""
    unsafe = check_safe_outbound_url(str(request.url))
    if unsafe is not None:
        raise _UnsafeRedirectError(f"重定向目标被安全校验拒绝：{unsafe}")


def get_client() -> httpx.Client:
    """整个进程共用一个 Client（禁止每请求新建，实测握手失败率 40—60%）。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    headers={"User-Agent": settings.CRAWL_USER_AGENT},
                    follow_redirects=True,
                    timeout=httpx.Timeout(settings.CRAWL_TIMEOUT_SECONDS, connect=10.0),
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
                    http2=False,
                    proxy=settings.proxy,
                    event_hooks={"request": [_verify_request_hook]},
                )
    return _client


def reset_client() -> None:
    """测试用：释放单例。"""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def min_interval_for(url: str) -> float:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith("news.google.com"):
        return settings.GOOGLE_NEWS_MIN_INTERVAL_SEC
    return settings.HOST_MIN_INTERVAL_SEC


def _guarded_get(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response:
    """同 host 串行 + 最小间隔。锁必须包住"等待 + 请求"整体（04 §7.2）。"""
    host = (urlsplit(url).hostname or "").lower()
    interval = min_interval_for(url)
    with _host_locks[host]:
        wait = interval - (time.monotonic() - _host_last[host])
        if wait > 0:
            time.sleep(wait)
        try:
            response = client.get(url, headers=headers, timeout=timeout)
        finally:
            _host_last[host] = time.monotonic()
    if response.status_code in RETRYABLE_STATUS:
        raise _RetryableResponse(response)
    return response


def _classify_status(status: int) -> str:
    if status in AUTH_STATUS:
        return "auth"
    if 400 <= status < 500:
        return "http_4xx"
    # 429 收尾：仍归到服务端一类（可稍后重试），不新增枚举值
    return "http_5xx"


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_blocked_ip(mapped):
        return True
    return False


def check_safe_outbound_url(url: str) -> str | None:
    """SSRF 防护：仅允许 http/https，拦截本机/私网/链路本地/云元数据/IPv4-mapped IPv6 及数字伪装 IP。"""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return "URL 格式非法"
    if parsed.scheme.lower() not in ("http", "https"):
        return f"不允许的协议：{parsed.scheme or '空'}（仅允许 http/https）"
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "URL 缺少主机名"
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local") or host.endswith(".internal"):
        return f"禁止访问本地或内网域名：{host}"
    # 拦截纯十进制/十六进制/八进制数字伪装 IP（如 http://2130706433 或 0x7f000001）
    if host.isdigit() or host.startswith("0x"):
        return f"禁止使用数字/十六进制伪装主机名：{host}"
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            return f"禁止访问内网或保留 IP：{host}"
    except ValueError:
        # 检查简写 IPv4（如 127.1）或含八进制段的 IPv4 形式
        parts = host.split(".")
        if parts and all(p.isdigit() or p.startswith("0x") for p in parts):
            return f"禁止使用非标准数字 IP：{host}"
    return None


def fetch(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_attempts: int | None = None,
) -> FetchOutcome:
    """抓一个 URL。成功返回 2xx/304；失败返回错误分类（含 SSRF 拦截）。"""
    unsafe_reason = check_safe_outbound_url(url)
    if unsafe_reason is not None:
        return FetchOutcome(
            error_kind="http_4xx",
            error=f"URL 安全校验拒绝：{unsafe_reason}",
            attempts=0,
            duration_ms=0,
        )
    timeout = timeout if timeout is not None else settings.CRAWL_TIMEOUT_SECONDS
    max_attempts = max_attempts if max_attempts is not None else settings.CRAWL_MAX_ATTEMPTS
    client = get_client()
    headers = dict(extra_headers or {})
    attempts = 0
    counter_lock = threading.Lock()
    started = time.monotonic()

    def _attempt() -> httpx.Response:
        nonlocal attempts
        with counter_lock:
            attempts += 1
        return _guarded_get(client, url, headers, timeout)

    retryer = Retrying(
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=wait_exponential(multiplier=0.6, min=0.6, max=4),
        retry=retry_if_exception_type(
            (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
                _RetryableResponse,
            )
        ),
        reraise=True,
    )

    try:
        response = retryer(_attempt)
    except _RetryableResponse as exc:  # 重试耗尽仍是可重试状态码
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchOutcome(
            status_code=exc.response.status_code,
            error_kind=_classify_status(exc.response.status_code),
            error=f"HTTP {exc.response.status_code}（重试 {attempts} 次后仍失败）",
            attempts=attempts,
            duration_ms=elapsed,
        )
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.TransportError,
    ) as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchOutcome(
            error_kind="connect",
            error=f"{type(exc).__name__}: {exc}",
            attempts=attempts,
            duration_ms=elapsed,
        )
    except _UnsafeRedirectError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchOutcome(
            error_kind="http_4xx",
            error=str(exc),
            attempts=attempts,
            duration_ms=elapsed,
        )
    except httpx.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchOutcome(
            error_kind="connect",
            error=f"{type(exc).__name__}: {exc}",
            attempts=attempts,
            duration_ms=elapsed,
        )

    elapsed = int((time.monotonic() - started) * 1000)
    status = response.status_code
    if status == 304:
        return FetchOutcome(
            status_code=304, not_modified=True, attempts=attempts, duration_ms=elapsed
        )
    if status in AUTH_STATUS:
        return FetchOutcome(
            status_code=status,
            error_kind="auth",
            error=f"HTTP {status}（需要凭据，不做重试）",
            attempts=attempts,
            duration_ms=elapsed,
        )
    if not 200 <= status < 300:
        # 非可重试的 4xx/3xx 已在 _guarded_get 之外，这里统一收口
        return FetchOutcome(
            status_code=status,
            error_kind=_classify_status(status),
            error=f"HTTP {status}",
            attempts=attempts,
            duration_ms=elapsed,
        )

    content = response.content
    truncated = False
    if len(content) > settings.MAX_RESPONSE_BYTES:
        content = content[: settings.MAX_RESPONSE_BYTES]
        truncated = True
        logger.warning("响应超过 %s 字节，已截断：%s", settings.MAX_RESPONSE_BYTES, url)

    return FetchOutcome(
        status_code=status,
        content=content,
        headers={k.lower(): v for k, v in response.headers.items()},
        attempts=attempts,
        duration_ms=elapsed,
        truncated=truncated,
    )
