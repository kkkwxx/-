"""独立 Worker 进程（`python -m app.worker`）。

口径：04-采集 §1.2 / §1.3 / §1.4。

* 与 API 分离：API 只写 ``task_run(pending)``，Worker 捞。
* **tick 只派发，绝不执行**：把活丢进线程池立即返回，否则慢源会把后续 tick 全拖垮。
* 三个 job：``tick``（30 s）、``poll_pending``（30 s）、``fetch_full_text``（60 s）。
"""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.models.pipeline import RawItem
from app.models.task import TaskRun
from app.services import collector, fulltext, recalc

logger = logging.getLogger(__name__)

# 源级并发 4（技术栈 §5.5）：再高会同时打多个 host，反而易触发限流
_executor = ThreadPoolExecutor(max_workers=settings.CRAWL_SOURCE_CONCURRENCY)

_shutdown = Event()


def _find_pending_recalc_runs(session) -> list[int]:
    rows = session.execute(
        select(TaskRun.id)
        .where(TaskRun.status == "pending", TaskRun.task_type == "recalc_levels")
        .order_by(TaskRun.id)
    ).all()
    return [row[0] for row in rows]


def _count_pending_full_text(session) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(RawItem)
            .where(RawItem.full_text_status == "pending", RawItem.url.is_not(None))
        ).scalar_one()
        or 0
    )


def tick() -> None:
    """30 s 一次：只查表 + 投递，立即返回。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        source_ids = collector.find_due_sources(session)
    except Exception:
        logger.exception("tick 查到期源失败")
        return
    finally:
        session.close()

    for source_id in source_ids:
        _executor.submit(collector.poll_source, source_id)  # 不 join、不 wait
    if source_ids:
        logger.info("本轮到期 %s 个源，已投递", len(source_ids))


def poll_pending() -> None:
    """30 s 一次：消化后台"立即采集"与"重算等级"产生的 pending 任务。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        poll_ids = collector.find_pending_runs(session)
        recalc_ids = _find_pending_recalc_runs(session)
    except Exception:
        logger.exception("poll_pending 查待处理任务失败")
        return
    finally:
        session.close()

    for run_id in poll_ids:
        _executor.submit(collector.run_pending_task, run_id)
    for run_id in recalc_ids:
        _executor.submit(recalc.run_recalc_task, run_id)
    if poll_ids or recalc_ids:
        logger.info(
            "待处理任务：poll=%s recalc=%s", len(poll_ids), len(recalc_ids)
        )


def fetch_full_text() -> None:
    """60 s 一次：有 pending 条目就开一个任务，一批最多 FULL_TEXT_BATCH_SIZE 条。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        pending = _count_pending_full_text(session)
        if pending <= 0:
            return
        run = TaskRun(
            task_type="fetch_full_text",
            source_id=None,
            status="running",
            triggered_by="scheduler",
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        run_id = run.id
    except Exception:
        session.rollback()
        logger.exception("fetch_full_text 建任务失败")
        return
    finally:
        session.close()

    logger.info("正文补全：待补 %s 条，投递 task_run=%s", pending, run_id)
    _executor.submit(fulltext.run_full_text_batch, run_id)


def _reclaim() -> None:
    session = SessionLocal()
    try:
        count = collector.reclaim_stale_runs(session)
        if count:
            logger.warning("回收卡住的 running 任务 %s 条", count)
    except Exception:
        logger.exception("回收卡住任务失败")
    finally:
        session.close()


def _handle_sigterm(_signum, _frame) -> None:
    logger.info("收到 SIGTERM，准备退出")
    _shutdown.set()


def _shutdown_all(scheduler: BlockingScheduler | None = None) -> None:
    _shutdown.set()
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("scheduler.shutdown 失败")
    _executor.shutdown(wait=False, cancel_futures=False)
    logger.info("Worker 已停止")


def main() -> None:
    setup_logging()
    logger.info(
        "Worker 启动：并发 %s，轮询 tick 30s / pending 30s / 全文 60s",
        settings.CRAWL_SOURCE_CONCURRENCY,
    )
    _reclaim()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        tick,
        "interval",
        seconds=30,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="tick",
    )
    scheduler.add_job(
        poll_pending,
        "interval",
        seconds=30,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="poll_pending",
    )
    scheduler.add_job(
        fetch_full_text,
        "interval",
        seconds=60,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="fetch_full_text",
    )

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, AttributeError):  # 非主线程 / 平台不支持时忽略
        logger.debug("SIGTERM 处理注册失败，忽略")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到中断信号，开始退出")
    finally:
        _shutdown_all(scheduler)


if __name__ == "__main__":
    main()
