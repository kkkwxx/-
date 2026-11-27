"""重算历史文章等级（03 §8.4 C1）。

``article.source_level`` / ``source_level_reason`` 是**落库值**，改域名规则后
不重算会出现同一域名新旧数据等级不一致。范围只到未发布文章：``published`` /
``unpublished`` 不追溯，避免改已上线内容。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.pipeline import Article, RawItem
from app.models.source import Source
from app.models.task import TaskRun
from app.services.collector import load_domain_rules, to_source_config
from app.services.dto import RawItemDraft
from app.services.grading import grade_article_source

logger = logging.getLogger(__name__)

# 03 §8.4 C1：这些状态不追溯
FROZEN_STATUSES = ("published", "unpublished")


def recalc_article_levels(session: Session, *, dry_run: bool = False) -> dict:
    """用**当前**规则表重算未发布文章的等级（不做规则版本化）。"""
    rules = load_domain_rules(session)
    sources = {
        source.id: source
        for source in session.execute(select(Source)).scalars().all()
    }

    rows = session.execute(
        select(Article, RawItem)
        .join(RawItem, RawItem.id == Article.raw_item_id)
        .where(Article.status.not_in(FROZEN_STATUSES))
    ).all()

    scanned = 0
    changed = 0
    for article, raw in rows:
        source = sources.get(raw.source_id)
        if source is None:
            # 源被删掉的极端情况（理论上有 FK RESTRICT 挡着），跳过
            continue
        scanned += 1
        draft = RawItemDraft(
            source_id=raw.source_id,
            url=raw.url,
            title=raw.raw_title,
            fetched_at=raw.fetched_at,
            resolved_url=raw.resolved_url,
            publisher_url=article.publisher_url or raw.publisher_url,
            publisher_name=article.publisher_name or raw.publisher_name,
            summary=raw.raw_summary,
        )
        grade = grade_article_source(draft, to_source_config(source), rules)
        if grade.level == article.source_level and grade.reason == article.source_level_reason:
            continue
        changed += 1
        if not dry_run:
            old_level = article.source_level
            article.source_level = grade.level
            article.source_level_reason = grade.reason
            # 同步调整 review_priority 中的等级权重差值（A=30, B=15, C=0）
            level_score = {"A": 30, "B": 15, "C": 0}
            delta = level_score.get(grade.level, 0) - level_score.get(old_level, 0)
            if delta:
                article.review_priority = int(article.review_priority or 0) + delta

    skipped_published = session.execute(
        select(func.count())
        .select_from(Article)
        .where(Article.status.in_(FROZEN_STATUSES))
    ).scalar_one()

    if not dry_run:
        session.commit()

    return {
        "scanned": scanned,
        "changed": changed,
        "skipped_published": int(skipped_published or 0),
    }


def run_recalc_task(task_run_id: int) -> None:
    """Worker 入口：自带 session，收口 task_run。"""
    from datetime import datetime, timezone

    session = SessionLocal()
    try:
        run = session.get(TaskRun, task_run_id)
        if run is None or run.task_type != "recalc_levels":
            return
        if run.status not in ("pending", "running"):
            return
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        session.commit()

        try:
            stats = recalc_article_levels(session)
        except Exception as exc:
            session.rollback()
            run = session.get(TaskRun, task_run_id)
            if run is not None:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
            logger.exception("重算等级失败 task_run_id=%s", task_run_id)
            return

        run = session.get(TaskRun, task_run_id)
        if run is not None:
            run.status = "success"
            run.stats_json = stats
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("run_recalc_task 失败 task_run_id=%s", task_run_id)
    finally:
        session.close()
