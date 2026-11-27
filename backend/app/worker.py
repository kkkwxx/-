"""独立 Worker 进程（`python -m app.worker`）。

优化升级：
- 五个 job：``tick``（30 s）、``poll_pending``（30 s）、``fetch_full_text``（60 s）、
  ``preprocess``（60 s，含 06 去重与时效）、``generate_content``（60 s，07 AI 文案加工）；
- 为批处理任务增加数据库级防重入锁（``_has_running_task``），杜绝单批耗时超 60s 时的重复投递与重复扣费；
- 启动时回收超时 ``running`` 的 ``task_run`` 以及卡在 ``processing`` 的僵尸 ``article``。
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
from app.services import collector, fulltext, generator, preprocess, recalc, scheduler_control

logger = logging.getLogger(__name__)

# 源级并发 4（技术栈 §5.5）：再高会同时打多个 host，反而易触发限流
_executor = ThreadPoolExecutor(max_workers=settings.CRAWL_SOURCE_CONCURRENCY)

_shutdown = Event()


def _has_running_task(session, task_type: str) -> bool:
    """防重入锁：同类全局批任务仍有 running 记录时本轮跳过投递。"""
    exists = session.execute(
        select(TaskRun.id)
        .where(TaskRun.task_type == task_type, TaskRun.status == "running")
        .limit(1)
    ).first()
    return exists is not None


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
            .where(
                RawItem.full_text_status == "pending",
                RawItem.url.is_not(None),
                RawItem.resolved_url.is_not(None),
            )
        ).scalar_one()
        or 0
    )


def tick() -> None:
    """30 s 一次：受调度策略控制（manual/静默窗跳过；fixed_times 命中定点触发一次）。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        # 1) 固定时间点模式：检查是否命中未执行的定点槽
        slot_key, cfg = scheduler_control.claim_due_fixed_slot(session)
        if slot_key is not None:
            action = (
                "full"
                if (cfg.crawl_enabled and cfg.ai_enabled)
                else "crawl_only"
                if cfg.crawl_enabled
                else "ai_only"
            )
            try:
                run = scheduler_control.start_pipeline_run(
                    session,
                    action=action,  # type: ignore[arg-type]
                    max_items=int(cfg.max_ai_items_per_run or 5),
                    triggered_by="scheduler",
                    spawn_thread=True,
                )
                logger.info(
                    "命中定点调度槽 %s，已启动 pipeline_run=%s（action=%s max_items=%s）",
                    slot_key,
                    run.id,
                    action,
                    cfg.max_ai_items_per_run,
                )
            except Exception:
                logger.warning("定点调度槽 %s 触发跳过（可能已有任务运行中）", slot_key)
            return

        # 2) 活跃时间窗模式：仅在允许采集时段拉取到期源
        allowed, _max_items = scheduler_control.should_worker_run_auto(
            session, stage="crawl"
        )
        if not allowed:
            return
        source_ids = collector.find_due_sources(session)
    except Exception:
        logger.exception("tick 查到期源失败")
        return
    finally:
        session.close()

    for source_id in source_ids:
        _executor.submit(collector.poll_source, source_id)
    if source_ids:
        logger.info("本轮到期 %s 个源，已投递", len(source_ids))


def poll_pending() -> None:
    """30 s 一次：消化后台“立即采集”与“重算等级”产生的 pending 任务。"""
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
        logger.info("待处理任务：poll=%s recalc=%s", len(poll_ids), len(recalc_ids))


def fetch_full_text() -> None:
    """60 s 一次：受调度策略控制，仅在允许自动采集时段补全正文。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        allowed, _max_items = scheduler_control.should_worker_run_auto(
            session, stage="crawl"
        )
        if not allowed:
            return
        if _has_running_task(session, "fetch_full_text"):
            logger.debug("fetch_full_text 已有批次运行中，本轮跳过")
            return
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


def run_preprocess() -> None:
    """60 s 一次：受调度策略控制，仅在允许自动 AI 时段且按条数上限执行预处理。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        allowed, max_items = scheduler_control.should_worker_run_auto(
            session, stage="ai"
        )
        if not allowed:
            return
        if _has_running_task(session, "preprocess"):
            logger.debug("preprocess 已有批次运行中，本轮跳过")
            return
        pending = preprocess.count_pending(session)
    except Exception:
        logger.exception("preprocess 查询待处理条目失败")
        return
    finally:
        session.close()

    if pending <= 0:
        return
    eff_limit = min(int(settings.PREPROCESS_BATCH_SIZE), max_items)
    logger.info(
        "预处理：待处理 %s 条，投递一批（上限 %s）",
        pending,
        eff_limit,
    )
    _executor.submit(preprocess.run_preprocess_batch, eff_limit)


def run_generate() -> None:
    """60 s 一次：受调度策略控制，仅在允许自动 AI 时段且按条数上限执行文案生成。"""
    if _shutdown.is_set():
        return
    session = SessionLocal()
    try:
        allowed, max_items = scheduler_control.should_worker_run_auto(
            session, stage="ai"
        )
        if not allowed:
            return
        if _has_running_task(session, "generate_content"):
            logger.debug("generate_content 已有批次运行中，本轮跳过")
            return
        pending = generator.count_judged(session)
    except Exception:
        logger.exception("generate_content 查询待加工条目失败")
        return
    finally:
        session.close()

    if pending <= 0:
        return
    eff_limit = min(int(settings.GENERATE_BATCH_SIZE), max_items)
    logger.info(
        "AI 加工：待加工 %s 条，投递一批（上限 %s）",
        pending,
        eff_limit,
    )
    _executor.submit(generator.run_generate_batch, eff_limit)


def _reclaim() -> None:
    session = SessionLocal()
    try:
        count = collector.reclaim_stale_runs(session)
        if count:
            logger.warning("回收卡住的 running 任务 / processing 文章共 %s 条", count)
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
        "Worker 启动：并发 %s，job：tick 30s / poll_pending 30s / fetch_full_text 60s / "
        "preprocess 60s / generate_content 60s",
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
    scheduler.add_job(
        run_preprocess,
        "interval",
        seconds=60,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="preprocess",
    )
    scheduler.add_job(
        run_generate,
        "interval",
        seconds=60,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="generate_content",
    )
    # 周期性回收超时 running 任务与僵尸 processing 文章，避免长运行过程中卡死
    scheduler.add_job(
        _reclaim,
        "interval",
        minutes=10,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        id="reclaim_stale",
    )

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, AttributeError):
        logger.debug("SIGTERM 处理注册失败，忽略")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到中断信号，开始退出")
    finally:
        _shutdown_all(scheduler)


if __name__ == "__main__":
    main()
