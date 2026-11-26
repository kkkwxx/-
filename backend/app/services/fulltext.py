"""正文补全（独立任务）。口径：04-采集 §11 / §13。

四级回退链：``skipped``（RSS 自带 content:encoded，在 collector 里定）→
``ok``（抽出 ≥200 字）→ ``thin``（抽出 <200 字，回退 summary）→
``blocked``（抓不到 / 401 / 403 / 451，回退 summary）。

**失败只记日志，绝不改 source 健康度**——"feed 能不能拉"与"正文能不能抽"
是两个信号（04 §11.1）。任何一条都必须从 ``pending`` 推向终态，不无限重试。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import trafilatura
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.pipeline import RawItem
from app.models.task import TaskRun
from app.services.fetch import fetch

logger = logging.getLogger(__name__)

# 04 §11.2：抽出 200 字及以上才算 ok
MIN_FULL_TEXT_CHARS = 200

_BLOCKED_STATUS = frozenset({401, 403, 451})


def find_pending_item_ids(session: Session, limit: int | None = None) -> list[int]:
    """待补队列就用 ``raw_item`` 自身（04 §11.3），不新加表。"""
    batch = int(limit if limit is not None else settings.FULL_TEXT_BATCH_SIZE)
    if batch <= 0:
        return []
    rows = session.execute(
        select(RawItem.id)
        .where(
            RawItem.full_text_status == "pending",
            RawItem.url.is_not(None),
        )
        .order_by(RawItem.fetched_at.asc())
        .limit(batch)
    ).all()
    return [row[0] for row in rows]


def _fallback_summary(item: RawItem) -> str | None:
    """回退 RSS summary；空则 None（04 §11.2）。"""
    summary = (item.raw_summary or "").strip()
    return summary or None


def _decode(html_bytes: bytes) -> str:
    try:
        return html_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return html_bytes.decode("utf-8", errors="replace")


def extract_full_text(item_id: int) -> None:
    """补一条正文。自带 session，任何异常只记日志。"""
    session = SessionLocal()
    try:
        item = session.get(RawItem, item_id)
        if item is None or item.full_text_status != "pending":
            return
        if not item.url:
            # 理论上被 find_pending_item_ids 过滤掉了，兜底防止卡在 pending
            item.full_text_status = "blocked"
            item.full_text = _fallback_summary(item)
            session.commit()
            return

        outcome = fetch(
            item.url,
            timeout=settings.CRAWL_TIMEOUT_SECONDS,
            max_attempts=settings.CRAWL_MAX_ATTEMPTS,
        )

        if outcome.error_kind:
            # connect / http_4xx / http_5xx / auth 一律终态 blocked：
            # 401/403/451 是最典型的，但其它失败也不能留在 pending 里反复重试
            item.full_text_status = "blocked"
            item.full_text = _fallback_summary(item)
            if outcome.error_kind == "auth" or outcome.status_code in _BLOCKED_STATUS:
                logger.info(
                    "正文补全被拒 item_id=%s status=%s url=%s",
                    item_id,
                    outcome.status_code,
                    item.url,
                )
            else:
                logger.info(
                    "正文补全失败 item_id=%s kind=%s error=%s",
                    item_id,
                    outcome.error_kind,
                    outcome.error,
                )
            session.commit()
            return

        html_text = _decode(outcome.content or b"")
        text = trafilatura.extract(
            html_text,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
        )
        text = (text or "").strip()

        if len(text) >= MIN_FULL_TEXT_CHARS:
            item.full_text_status = "ok"
            item.full_text = text
        else:
            # thin：抽出太少，回退 RSS summary
            item.full_text_status = "thin"
            item.full_text = _fallback_summary(item)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("正文补全异常 item_id=%s（不计入源健康度）", item_id)
    finally:
        session.close()


def run_full_text_batch(task_run_id: int | None = None) -> int:
    """补一批（最多 ``FULL_TEXT_BATCH_SIZE`` 条），串行执行。

    返回处理条数。单条异常不中断整批。
    """
    session = SessionLocal()
    try:
        item_ids = find_pending_item_ids(session, settings.FULL_TEXT_BATCH_SIZE)
    finally:
        session.close()

    processed = 0
    for item_id in item_ids:
        try:
            extract_full_text(item_id)
            processed += 1
        except Exception:  # extract_full_text 自己已兜底，这里再保一层
            logger.exception("整批补全中单条失败 item_id=%s", item_id)

    if task_run_id is not None:
        _close_task_run(task_run_id, processed)
    return processed


def _close_task_run(task_run_id: int, processed: int) -> None:
    """收口 task_run。本任务不影响源健康度，只记条数。"""
    session = SessionLocal()
    try:
        run = session.get(TaskRun, task_run_id)
        if run is None:
            return
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.stats_json = {"processed": processed}
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("收口 fetch_full_text 任务失败 task_run_id=%s", task_run_id)
    finally:
        session.close()
