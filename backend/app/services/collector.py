"""采集执行体：定级 + 落地 + 健康回写 + task_run 收口（04 §6 / §10）。"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.pipeline import Article, RawItem
from app.models.source import Source, SourceDomainRule, UnknownDomain
from app.models.task import TaskRun
from app.services.adapters import clean_text, get_adapter
from app.services.domain import registrable_domain
from app.services.dto import FetchResult, RawItemDraft, SourceConfig
from app.services.grading import grade_article_source
from app.services.health import apply_poll_result

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = ("utm_", "ref", "ref_", "spm", "from", "share", "fbclid", "gclid")
_TRACKING_RE = re.compile(r"^https?://", re.I)


def canonicalize(url: str) -> str:
    """只删追踪参数，不改路径与协议（04 §9.3）。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not any(k.lower().startswith(prefix) for prefix in _TRACKING_PARAMS)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def normalize_for_hash(value: str | None) -> str:
    return clean_text(value).lower()


def item_url_hash(draft: RawItemDraft) -> str:
    return sha256(
        draft.resolved_url
        or draft.url
        or draft.publisher_url
        or f"{draft.source_id}:{draft.title}"
    )


def load_domain_rules(session: Session) -> dict[str, str]:
    rows = session.execute(
        select(SourceDomainRule.domain, SourceDomainRule.level).where(
            SourceDomainRule.enabled.is_(True)
        )
    ).all()
    return {domain: level for domain, level in rows}


def to_source_config(source: Source) -> SourceConfig:
    return SourceConfig(
        id=source.id,
        code=source.code,
        type=source.type,
        level=source.level,
        expected_domains=tuple(source.expected_domains or ()),
    )


def upsert_unknown_domain(
    session: Session, domain: str, *, title: str | None, source_code: str | None, now: datetime
) -> None:
    """规则 6 触发时 upsert（03 §6.6）。"""
    row = session.execute(
        select(UnknownDomain).where(UnknownDomain.domain == domain)
    ).scalar_one_or_none()
    if row is None:
        session.add(
            UnknownDomain(
                domain=domain,
                hit_count=1,
                first_seen_at=now,
                last_seen_at=now,
                sample_title=(title or "")[:500] or None,
                sample_source_code=source_code,
            )
        )
    else:
        row.hit_count += 1
        row.last_seen_at = now
        if title and not row.sample_title:
            row.sample_title = title[:500]
        if source_code and not row.sample_source_code:
            row.sample_source_code = source_code


def persist_items(
    session: Session,
    source: Source,
    result: FetchResult,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """逐条定级 + 落库。返回 task_run.stats_json 的计数部分（04 §10.2）。"""
    now = now or datetime.now(timezone.utc)
    rules = load_domain_rules(session)
    source_cfg = to_source_config(source)
    cutoff = now - timedelta(days=settings.MAX_AGE_DAYS)
    stats = {"fetched": len(result.items), "inserted": 0, "skipped_dup": 0, "skipped_old": 0}

    for draft in result.items:
        if draft.published_at and draft.published_at < cutoff:
            stats["skipped_old"] += 1
            continue

        url_hash = item_url_hash(draft)
        existing = session.execute(
            select(RawItem).where(
                RawItem.source_id == source.id, RawItem.url_hash == url_hash
            )
        ).scalar_one_or_none()
        if existing is not None:
            stats["skipped_dup"] += 1
            continue

        dup_article = session.execute(
            select(Article).where(Article.url_hash == url_hash)
        ).scalar_one_or_none()
        if dup_article is not None:
            stats["skipped_dup"] += 1
            dup_sources = list(dup_article.dup_sources or [])
            if source.id not in dup_sources:
                dup_sources.append(source.id)
                dup_article.dup_sources = dup_sources
            continue

        grade = grade_article_source(draft, source_cfg, rules)
        if grade.unknown_domain:
            upsert_unknown_domain(
                session,
                grade.unknown_domain,
                title=draft.title,
                source_code=source.code,
                now=now,
            )

        has_content = bool(draft.content_encoded)
        raw = RawItem(
            source_id=source.id,
            url=draft.url,
            resolved_url=draft.resolved_url,
            publisher_url=draft.publisher_url,
            publisher_name=(draft.publisher_name or None) and draft.publisher_name[:255],
            raw_title=draft.title,
            raw_summary=draft.summary,
            content_encoded=draft.content_encoded,
            full_text=draft.content_encoded if has_content else None,
            full_text_status="skipped" if has_content else "pending",
            published_at=draft.published_at,
            published_raw=(draft.published_raw or None) and draft.published_raw[:128],
            author=draft.author,
            fetched_at=draft.fetched_at or now,
            extra=draft.extra or {},
            url_hash=url_hash,
        )
        session.add(raw)
        session.flush()

        canonical = canonicalize(draft.resolved_url) if draft.resolved_url else None
        session.add(
            Article(
                raw_item_id=raw.id,
                title=clean_text(draft.title) or draft.title,
                summary=draft.summary,
                publisher_name=raw.publisher_name,
                publisher_url=draft.publisher_url,
                original_url=draft.resolved_url,
                canonical_url=canonical,
                source_level=grade.level,
                source_level_reason=grade.reason,
                resolve_status="resolved" if draft.resolved_url else "unresolved",
                url_hash=url_hash,
                content_hash=sha256(
                    normalize_for_hash(draft.title)
                    + "|"
                    + normalize_for_hash(draft.summary)
                ),
                title_fingerprint=normalize_for_hash(draft.title),
                dup_sources=[],
                status="ingested",
            )
        )
        stats["inserted"] += 1

    return stats


def _run_adapter(source: Source) -> FetchResult:
    adapter = get_adapter(source.type)
    if adapter is None:
        return FetchResult(
            error_kind="http_4xx", error=f"未支持的源类型：{source.type}"
        )
    url = source.url or ""
    if not url and source.query_template:
        from app.services.adapters import build_google_news_url

        url = build_google_news_url(source.query_template)
    if not url:
        return FetchResult(error_kind="http_4xx", error="该源没有配置 url")

    if source.type == "api":
        return adapter.fetch(url=url, source_id=source.id)
    return adapter.fetch(
        url=url,
        source_id=source.id,
        source_type=source.type,
        etag=source.etag,
        last_modified=source.last_modified,
        extra_headers=source.extra_headers or {},
        title_suffix_rules=source.title_suffix_rules or [],
    )


def execute_poll(session: Session, run: TaskRun, source: Source) -> None:
    """两条路径（tick / pending）共用这段执行代码（04 §6）。"""
    now = datetime.now(timezone.utc)
    run.status = "running"
    run.started_at = now
    session.flush()

    result = _run_adapter(source)
    stats: dict = dict(result.stats or {})
    stats["attempts"] = result.attempts
    stats["duration_ms"] = result.duration_ms

    if result.error_kind:
        stats["error_kind"] = result.error_kind
        run.status = "failed"
        run.error = (result.error or "")[:2000]
        apply_poll_result(
            session,
            source,
            ok=False,
            error_kind=result.error_kind,
            error=result.error,
            fetched=0,
            now=now,
        )
    else:
        persisted = persist_items(session, source, result, now=now)
        stats.update(persisted)
        run.status = "success"
        # 条件请求凭证只在成功时更新
        if result.not_modified:
            apply_poll_result(
                session, source, ok=True, error_kind=None, error=None,
                fetched=int(stats.get("fetched") or 0), now=now,
            )
        else:
            apply_poll_result(
                session, source, ok=True, error_kind=None, error=None,
                fetched=int(stats.get("fetched") or 0), now=now,
            )

    run.stats_json = stats
    run.finished_at = datetime.now(timezone.utc)


def poll_source(source_id: int, triggered_by: str = "scheduler") -> int | None:
    """Worker 入口：自带 session，建 run → 执行 → 收口。"""
    session = SessionLocal()
    try:
        source = session.get(Source, source_id)
        if source is None or not source.enabled:
            return None
        busy = session.execute(
            select(TaskRun.id).where(
                TaskRun.source_id == source_id,
                TaskRun.status.in_(("pending", "running")),
            )
        ).first()
        if busy is not None:
            return None
        run = TaskRun(
            task_type="poll",
            source_id=source_id,
            status="running",
            triggered_by=triggered_by,
        )
        session.add(run)
        session.commit()
        run = session.get(TaskRun, run.id)
        source = session.get(Source, source_id)
        execute_poll(session, run, source)
        session.commit()
        return run.id
    except Exception:  # 本地错误不计入源健康度（04 §13.1）
        session.rollback()
        logger.exception("poll_source 失败 source_id=%s", source_id)
        return None
    finally:
        session.close()


def run_pending_task(run_id: int) -> None:
    """API 插入的 pending 任务：改成 running 后走同一段执行代码。"""
    session = SessionLocal()
    try:
        run = session.get(TaskRun, run_id)
        if run is None or run.status != "pending" or run.task_type != "poll":
            return
        source = session.get(Source, run.source_id) if run.source_id else None
        if source is None:
            run.status = "failed"
            run.error = "SOURCE_NOT_FOUND"
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            return
        execute_poll(session, run, source)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("run_pending_task 失败 run_id=%s", run_id)
    finally:
        session.close()


DUE_SQL = text(
    """
    SELECT s.id
    FROM source s
    WHERE s.enabled = true
      AND (s.last_poll_at IS NULL
           OR s.last_poll_at < now() - (s.poll_interval_min * interval '1 minute') * 0.9)
      AND NOT EXISTS (
            SELECT 1 FROM task_run t
            WHERE t.source_id = s.id AND t.status IN ('pending', 'running'))
    ORDER BY s.last_poll_at NULLS FIRST
    """
)


def find_due_sources(session: Session) -> list[int]:
    return [row[0] for row in session.execute(DUE_SQL).all()]


def find_pending_runs(session: Session) -> list[int]:
    rows = session.execute(
        select(TaskRun.id)
        .where(TaskRun.status == "pending", TaskRun.task_type == "poll")
        .order_by(TaskRun.id)
    ).all()
    return [row[0] for row in rows]


def reclaim_stale_runs(session: Session, older_than_minutes: int = 30) -> int:
    """Worker 启动清理：卡在 running 的任务会让该源永久不再被调度（04 §6）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    rows = session.execute(
        select(TaskRun).where(
            TaskRun.status == "running",
            TaskRun.started_at.is_not(None),
            TaskRun.started_at < cutoff,
        )
    ).scalars().all()
    for run in rows:
        run.status = "failed"
        run.error = "worker restarted"
        run.finished_at = datetime.now(timezone.utc)
    if rows:
        session.commit()
    return len(rows)
