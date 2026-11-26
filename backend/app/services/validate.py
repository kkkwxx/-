"""探活（validate）。

口径：03-信源管理 §8.4 V1/V2。

- **参数必须独立于采集**：超时 ``VALIDATE_TIMEOUT_SECONDS``（8 s）、
  重试 ``VALIDATE_MAX_ATTEMPTS``（1 次）、并发 4。
- **不写业务数据**：这里一个 ``raw_item`` 都不落库，``last_validated_at``
  由调用方（路由）写。
- 探活"源不可用"不是接口错误，结果放在返回值里。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.config import settings
from app.services.adapters import FeedAdapter, build_google_news_url, elapsed_ms

logger = logging.getLogger(__name__)

# preview 最多返回 3 条（03 §8.3 ValidateResult）
PREVIEW_SIZE = 3

# 03 §7.5 六种 error_kind 对应的中文动作建议，直接给管理员看
SUGGESTIONS: dict[str, str] = {
    "connect": "网络层不可达，稍后重试；持续失败请检查地址或换机复测",
    "http_4xx": "返回 4xx，源地址可能已迁移，请确认新地址",
    "http_5xx": "服务端错误，稍后重试",
    "auth": "需要凭据。请配置 api_key_env 后重置健康度",
    "parse": "返回内容不是合法 feed，请确认地址",
    "empty_feed": "返回 0 条。feed 可能是空壳页，或该源近期确实无更新",
}

API_NOT_IMPLEMENTED = "ApiAdapter 本期未实现：该源需要 API Key（决策 C3）"
MISSING_URL = "该源没有配置 url 或 query_template，无法探活"


def _suggestion(error_kind: str | None, http_status: int | None) -> str:
    """按 error_kind 给一句可执行的动作建议（03 §7.5）。"""
    if not error_kind:
        return ""
    if error_kind == "http_4xx" and http_status:
        return f"返回 HTTP {http_status}，源地址可能已迁移，请确认新地址"
    if error_kind == "http_5xx" and http_status:
        return f"服务端错误（HTTP {http_status}），稍后重试"
    return SUGGESTIONS.get(error_kind, "探活失败，请检查该源配置")


def _base_result(
    *,
    source_id: int | None,
    code: str | None,
    started: float,
    ok: bool = False,
    http_status: int | None = None,
    entries: int = 0,
    error_kind: str | None = None,
    error: str | None = None,
    preview: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "code": code,
        "ok": ok,
        "http_status": http_status,
        "entries": entries,
        "duration_ms": elapsed_ms(started),
        "error_kind": error_kind,
        "error": error,
        "suggestion": _suggestion(error_kind, http_status),
        "preview": preview or [],
    }


def probe_source(
    *,
    source_type: str,
    url: str | None,
    query_template: str | None = None,
    extra_headers: dict | None = None,
    api_key_env: str | None = None,
    source_id: int | None = None,
    code: str | None = None,
) -> dict:
    """探一个源。返回 dict（见 03 §8.3 ValidateResult）。"""
    started = time.monotonic()

    # api 类型本期没有适配器（决策 C3），直接给 auth 结论，不发请求
    if (source_type or "").strip() == "api":
        result = _base_result(
            source_id=source_id,
            code=code,
            started=started,
            error_kind="auth",
            error=API_NOT_IMPLEMENTED,
        )
        if api_key_env:
            result["error"] = f"{API_NOT_IMPLEMENTED}（期望环境变量：{api_key_env}）"
        return result

    target = (url or "").strip() or None
    if target is None and source_type == "google_news" and query_template:
        target = build_google_news_url(query_template)

    if target is None:
        logger.info("探活跳过：source_id=%s code=%s 无 url", source_id, code)
        return _base_result(
            source_id=source_id,
            code=code,
            started=started,
            error_kind="http_4xx",
            error=MISSING_URL,
        )

    try:
        fetched = FeedAdapter().fetch(
            url=target,
            source_id=source_id or 0,
            source_type=source_type or "rss",
            extra_headers=extra_headers or {},
            timeout=settings.VALIDATE_TIMEOUT_SECONDS,
            max_attempts=settings.VALIDATE_MAX_ATTEMPTS,
        )
    except Exception as exc:  # 探活绝不能把异常抛给路由
        logger.warning("探活异常 source_id=%s url=%s: %s", source_id, target, exc)
        return _base_result(
            source_id=source_id,
            code=code,
            started=started,
            error_kind="connect",
            error=f"{type(exc).__name__}: {exc}",
        )

    entries = len(fetched.items)
    stats = fetched.stats or {}
    raw_status = stats.get("http_status")
    http_status = int(raw_status) if isinstance(raw_status, int) else None
    preview = [
        {
            "title": item.title,
            "published": item.published_at.isoformat() if item.published_at else None,
            "link": item.url,
        }
        for item in fetched.items[:PREVIEW_SIZE]
    ]
    # ok = 没有 error_kind 且确实有条目（03 §8.3）
    ok = fetched.error_kind is None and entries > 0
    return _base_result(
        source_id=source_id,
        code=code,
        started=started,
        ok=ok,
        http_status=http_status,
        entries=entries,
        error_kind=fetched.error_kind,
        error=fetched.error,
        preview=preview,
    )


def _safe_probe(probe_kwargs: dict) -> dict:
    """probe_many 的单条包装：任何异常都收敛成 ok=False / connect。"""
    try:
        return probe_source(**probe_kwargs)
    except TypeError as exc:  # 调用方传错参数，同样不抛出
        logger.warning("探活参数错误: %s", exc)
        return {
            "source_id": probe_kwargs.get("source_id"),
            "code": probe_kwargs.get("code"),
            "ok": False,
            "http_status": None,
            "entries": 0,
            "duration_ms": 0,
            "error_kind": "connect",
            "error": f"探活参数错误: {exc}",
            "suggestion": _suggestion("connect", None),
            "preview": [],
        }
    except Exception as exc:  # noqa: BLE001 - 兜底
        logger.warning("探活失败: %s", exc)
        return {
            "source_id": probe_kwargs.get("source_id"),
            "code": probe_kwargs.get("code"),
            "ok": False,
            "http_status": None,
            "entries": 0,
            "duration_ms": 0,
            "error_kind": "connect",
            "error": f"{type(exc).__name__}: {exc}",
            "suggestion": _suggestion("connect", None),
            "preview": [],
        }


def probe_many(items: list[dict], concurrency: int = 4) -> list[dict]:
    """并发探活。``items`` 的键与 ``probe_source`` 的关键字参数一致。"""
    if not items:
        return []
    workers = max(1, min(int(concurrency or 1), len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_safe_probe, dict(item)) for item in items]
        # 按输入顺序收集，保证结果与请求一一对应
        return [future.result() for future in futures]


def status_from_probe(result: dict) -> str:
    """探活结果 → 健康状态（ok / auth_error / starved / down）。"""
    error_kind = result.get("error_kind")
    entries = int(result.get("entries") or 0)
    if error_kind == "auth":
        return "auth_error"
    if result.get("ok"):
        return "ok" if entries > 0 else "starved"
    if error_kind == "empty_feed":
        # 200 且解析成功但 0 条：空转，不是故障（03 §7.1 starved）
        return "starved"
    if not error_kind and entries == 0:
        # 通得了但一条也没有：空转，不是故障
        return "starved"
    return "down"
