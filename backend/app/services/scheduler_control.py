"""采集与 AI 调度引擎 + 按需「立即获取」异步执行器。

核心能力：
1. 三种调度模式：
   - ``manual``（默认）：纯手动，Worker 不自动采集也不自动调用 AI，完全避免偷跑烧额度；
   - ``window``：活跃时间窗（如每周一至周日 09:00–22:00，支持跨午夜窗如 22:00–06:00），
     窗口内按采集开关 / AI 开关分别放行，窗口外自动静默；
   - ``fixed_times``：固定时间点触发（如每天 09:00、14:00、20:00 各跑一轮），
     每个时间槽通过 ``last_fixed_slot_key`` 幂等防重；
2. 「现在立即获取」异步执行器：
   - 支持 ``full``（全链路）、``crawl_only``（仅采集+补正文，0 Token）、``ai_only``（仅 AI 判定+生成）；
   - 严格受 ``max_items`` 条数上限约束，支持无独立 Worker 窗口时直接由后端守护线程异步执行并实时写 ``task_run`` 进度。
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.errors import VALIDATION_FAILED, AppError
from app.models.pipeline import Article, RawItem
from app.models.schedule import PipelineScheduleConfig
from app.models.source import Source
from app.models.task import TaskRun
from app.services import collector, fulltext, generator, preprocess

logger = logging.getLogger(__name__)

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MANUAL_LOCK = threading.Lock()


def resolve_tz(tz_name: str | None) -> Any:
    """解析时区；Windows 缺少 tzdata 时对 Asia/Shanghai 自动回退固定 UTC+8。"""
    name = (tz_name or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in ("Asia/Shanghai", "Asia/Chongqing", "PRC"):
            return timezone(timedelta(hours=8))
        return timezone.utc


def validate_hhmm(value: str, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not _HHMM_RE.match(cleaned):
        raise AppError(
            VALIDATION_FAILED,
            f"{field_name} 必须是 24 小时制 HH:MM 格式（如 09:00），收到：{value}",
            status_code=400,
        )
    return cleaned


def normalize_weekdays(days: list[int]) -> list[int]:
    cleaned = sorted({int(d) for d in days if 1 <= int(d) <= 7})
    if not cleaned:
        raise AppError(
            VALIDATION_FAILED,
            "至少需要选择 1 个允许运行的星期（1=周一 至 7=周日）",
            status_code=400,
        )
    return cleaned


def normalize_fixed_times(times: list[str]) -> list[str]:
    cleaned = sorted({validate_hhmm(t, "fixed_times") for t in times})
    if len(cleaned) > 24:
        raise AppError(
            VALIDATION_FAILED,
            "固定时间点最多配置 24 个",
            status_code=400,
        )
    return cleaned


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@dataclass(frozen=True)
class ScheduleEvaluation:
    local_now: datetime
    local_now_str: str
    crawl_active_now: bool
    ai_active_now: bool
    status_summary: str
    next_run_hint: str
    due_fixed_slot_key: str | None = None


def get_or_create_schedule(db: Session) -> PipelineScheduleConfig:
    """获取全局唯一调度配置行；若尚未初始化则自动创建默认纯手动（manual）配置。"""
    row = (
        db.execute(
            select(PipelineScheduleConfig).order_by(PipelineScheduleConfig.id.asc()).limit(1)
        )
        .scalars()
        .first()
    )
    if row is not None:
        return row
    row = PipelineScheduleConfig(
        mode="manual",
        crawl_enabled=True,
        ai_enabled=True,
        timezone="Asia/Shanghai",
        weekdays=[1, 2, 3, 4, 5, 6, 7],
        window_start="09:00",
        window_end="22:00",
        fixed_times=["09:00", "14:00", "20:00"],
        max_ai_items_per_run=5,
        updated_by="system",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _next_matching_day_prefix(offset: int) -> str:
    if offset == 0:
        return "今天"
    if offset == 1:
        return "明天"
    return f"{offset} 天后"


def evaluate_schedule(
    cfg: PipelineScheduleConfig, *, now: datetime | None = None
) -> ScheduleEvaluation:
    """根据当前时间和配置，计算此刻是否允许自动采集 / 自动 AI 以及下次触发提示。"""
    utc_now = now or datetime.now(timezone.utc)
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=timezone.utc)
    tz = resolve_tz(cfg.timezone)
    local_now = utc_now.astimezone(tz)
    local_now_str = local_now.strftime("%Y-%m-%d %H:%M:%S")
    cur_weekday = local_now.isoweekday()  # 1..7
    cur_minutes = local_now.hour * 60 + local_now.minute
    weekdays = set(int(d) for d in (cfg.weekdays or [1, 2, 3, 4, 5, 6, 7]))

    if cfg.mode == "manual":
        return ScheduleEvaluation(
            local_now=local_now,
            local_now_str=local_now_str,
            crawl_active_now=False,
            ai_active_now=False,
            status_summary="纯手动模式：自动采集与自动 AI 均已静默，仅在你点击「现在立即获取」时运行",
            next_run_hint="等待手动触发",
        )

    if not cfg.crawl_enabled and not cfg.ai_enabled:
        return ScheduleEvaluation(
            local_now=local_now,
            local_now_str=local_now_str,
            crawl_active_now=False,
            ai_active_now=False,
            status_summary="已暂停：自动采集开关与自动 AI 开关均处于关闭状态",
            next_run_hint="需至少开启一项自动开关",
        )

    if cfg.mode == "window":
        start_m = _hhmm_to_minutes(cfg.window_start or "09:00")
        end_m = _hhmm_to_minutes(cfg.window_end or "22:00")
        if start_m == end_m:
            in_time = True
        elif start_m < end_m:
            in_time = start_m <= cur_minutes < end_m
        else:
            # 跨午夜时段，如 22:00 - 06:00
            in_time = cur_minutes >= start_m or cur_minutes < end_m
        in_window = (cur_weekday in weekdays) and in_time

        crawl_now = in_window and bool(cfg.crawl_enabled)
        ai_now = in_window and bool(cfg.ai_enabled)

        if in_window:
            active_parts = []
            if crawl_now:
                active_parts.append("自动采集")
            if ai_now:
                active_parts.append(f"自动 AI（每批限 {cfg.max_ai_items_per_run} 条）")
            summary = f"活跃时间窗内（{cfg.window_start}–{cfg.window_end}）：当前运行 {' + '.join(active_parts)}"
            next_hint = f"当前运行中，将于 {cfg.window_end} 进入静默"
        else:
            summary = f"静默时段（活跃窗为 {cfg.window_start}–{cfg.window_end}）：当前暂停自动获取与 AI 消耗"
            next_hint = "未配置有效运行星期"
            for offset in range(0, 8):
                cand_day = local_now + timedelta(days=offset)
                if cand_day.isoweekday() not in weekdays:
                    continue
                if offset == 0 and cur_minutes >= start_m:
                    continue
                next_hint = f"{_next_matching_day_prefix(offset)} {cfg.window_start} 恢复运行"
                break

        return ScheduleEvaluation(
            local_now=local_now,
            local_now_str=local_now_str,
            crawl_active_now=crawl_now,
            ai_active_now=ai_now,
            status_summary=summary,
            next_run_hint=next_hint,
        )

    # fixed_times 模式：定点触发（2 分钟宽限窗口内命中且未执行过该时间槽）
    slots = sorted({t for t in (cfg.fixed_times or []) if _HHMM_RE.match(t)})
    due_slot_key: str | None = None
    if cur_weekday in weekdays:
        for slot in slots:
            slot_m = _hhmm_to_minutes(slot)
            if slot_m <= cur_minutes < slot_m + 2:
                candidate_key = f"{local_now.strftime('%Y-%m-%d')} {slot}"
                if cfg.last_fixed_slot_key != candidate_key:
                    due_slot_key = candidate_key
                break

    next_hint = "未配置固定时间点"
    if slots and weekdays:
        for offset in range(0, 8):
            cand_day = local_now + timedelta(days=offset)
            if cand_day.isoweekday() not in weekdays:
                continue
            for slot in slots:
                slot_m = _hhmm_to_minutes(slot)
                slot_key = f"{cand_day.strftime('%Y-%m-%d')} {slot}"
                if offset == 0 and (
                    slot_m < cur_minutes or cfg.last_fixed_slot_key == slot_key
                ):
                    continue
                next_hint = f"{_next_matching_day_prefix(offset)} {slot}"
                break
            if next_hint != "未配置固定时间点":
                break

    slots_text = "、".join(slots) if slots else "无"
    summary = (
        f"固定时间点模式（{slots_text}）：非定点时段静默，定点到达时自动执行一轮（AI 限 {cfg.max_ai_items_per_run} 条）"
    )
    return ScheduleEvaluation(
        local_now=local_now,
        local_now_str=local_now_str,
        crawl_active_now=bool(due_slot_key and cfg.crawl_enabled),
        ai_active_now=bool(due_slot_key and cfg.ai_enabled),
        status_summary=summary,
        next_run_hint=next_hint,
        due_fixed_slot_key=due_slot_key,
    )


def should_worker_run_auto(
    db: Session, *, stage: Literal["crawl", "ai"], now: datetime | None = None
) -> tuple[bool, int]:
    """供 worker.py 轮询任务调用：仅在 window 活跃窗内放行常规轮询 job。"""
    cfg = get_or_create_schedule(db)
    if cfg.mode != "window":
        return False, int(cfg.max_ai_items_per_run or 5)
    ev = evaluate_schedule(cfg, now=now)
    allowed = ev.crawl_active_now if stage == "crawl" else ev.ai_active_now
    return allowed, int(cfg.max_ai_items_per_run or 5)


def claim_due_fixed_slot(
    db: Session, *, now: datetime | None = None
) -> tuple[str | None, PipelineScheduleConfig]:
    """供 worker.py 定点巡检调用：若命中未执行的 fixed_times 时间槽，原子占用并返回。"""
    cfg = get_or_create_schedule(db)
    if cfg.mode != "fixed_times":
        return None, cfg
    ev = evaluate_schedule(cfg, now=now)
    if not ev.due_fixed_slot_key or (not cfg.crawl_enabled and not cfg.ai_enabled):
        return None, cfg
    cfg.last_fixed_slot_key = ev.due_fixed_slot_key
    db.commit()
    db.refresh(cfg)
    return ev.due_fixed_slot_key, cfg


def _update_run_stats(run_id: int, stats: dict[str, Any], *, status: str | None = None, error: str | None = None) -> None:
    session = SessionLocal()
    try:
        run = session.get(TaskRun, run_id)
        if run is None:
            return
        run.stats_json = dict(stats)
        if status is not None:
            run.status = status
            run.finished_at = datetime.now(timezone.utc)
        if error is not None:
            run.error = error[:2000]
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("更新 pipeline_run 进度失败 run_id=%s", run_id)
    finally:
        session.close()


def execute_pipeline_run(
    run_id: int,
    *,
    action: Literal["full", "crawl_only", "ai_only"],
    max_items: int,
    source_ids: list[int] | None = None,
    include_pending: bool = False,
) -> dict[str, Any]:
    """执行一次受控的全链路 / 仅采集 / 仅 AI 流水线并实时更新 TaskRun 统计。"""
    stats: dict[str, Any] = {
        "action": action,
        "max_items": max_items,
        "current_stage": "starting",
        "sources_polled": 0,
        "inserted": 0,
        "full_text_processed": 0,
        "promoted_pending": 0,
        "preprocessed": 0,
        "judged": 0,
        "generated": 0,
        "candidate": 0,
        "pending": 0,
        "tokens": 0,
    }
    _update_run_stats(run_id, stats)

    try:
        # 阶段 1：采集新闻 + 补正文（0 Token）
        if action in ("full", "crawl_only"):
            stats["current_stage"] = "crawling"
            _update_run_stats(run_id, stats)

            with SessionLocal() as db:
                stmt = select(Source.id).where(
                    Source.enabled.is_(True), Source.type != "manual"
                )
                if source_ids:
                    stmt = stmt.where(Source.id.in_(source_ids))
                target_sids = [row[0] for row in db.execute(stmt.order_by(Source.id.asc())).all()]

            for sid in target_sids:
                res = collector.poll_source(sid)
                stats["sources_polled"] += 1
                if isinstance(res, dict):
                    stats["inserted"] += int(res.get("inserted") or 0)
                _update_run_stats(run_id, stats)

            # 补齐新抓条目的正文
            stats["current_stage"] = "full_text"
            _update_run_stats(run_id, stats)
            with SessionLocal() as db:
                pending_ft = int(
                    db.execute(
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
                ft_run_id: int | None = None
                if pending_ft > 0:
                    ft_run = TaskRun(
                        task_type="fetch_full_text",
                        source_id=None,
                        status="running",
                        triggered_by="manual",
                        started_at=datetime.now(timezone.utc),
                    )
                    db.add(ft_run)
                    db.commit()
                    ft_run_id = ft_run.id
            if ft_run_id is not None:
                ft_stats = fulltext.run_full_text_batch(ft_run_id)
                if isinstance(ft_stats, dict):
                    stats["full_text_processed"] = int(ft_stats.get("processed") or 0)
                _update_run_stats(run_id, stats)

        # 阶段 2：AI 判定（preprocess）+ 文案生成（generate），严格受 max_items 限量保护
        if action in ("full", "ai_only"):
            stats["current_stage"] = "preprocess"
            _update_run_stats(run_id, stats)

            if include_pending:
                with SessionLocal() as db:
                    ingested_cnt = preprocess.count_pending(db)
                    need = max(0, max_items - ingested_cnt)
                    if need > 0:
                        promote_rows = (
                            db.execute(
                                select(Article)
                                .where(
                                    Article.status == "pending",
                                    Article.title_final.is_(None),
                                )
                                .order_by(Article.review_priority.desc(), Article.id.desc())
                                .limit(need)
                            )
                            .scalars()
                            .all()
                        )
                        for art in promote_rows:
                            art.status = "ingested"
                            art.reject_reason = None
                        if promote_rows:
                            db.commit()
                            stats["promoted_pending"] = len(promote_rows)

            p_stats = preprocess.run_preprocess_batch(limit=max_items)
            stats["preprocessed"] = int(p_stats.get("scanned") or 0)
            stats["judged"] = int(p_stats.get("judged") or 0)
            stats["tokens"] += int(p_stats.get("tokens") or 0)
            _update_run_stats(run_id, stats)

            stats["current_stage"] = "generate"
            _update_run_stats(run_id, stats)
            g_stats = generator.run_generate_batch(limit=max_items)
            stats["generated"] = int(g_stats.get("scanned") or 0)
            stats["candidate"] = int(g_stats.get("candidate") or 0)
            stats["pending"] = int(g_stats.get("pending") or 0)
            stats["tokens"] += int(g_stats.get("tokens") or 0)

        stats["current_stage"] = "done"
        _update_run_stats(run_id, stats, status="success")
        return stats
    except Exception as exc:
        logger.exception("手动/定点流水线执行失败 run_id=%s", run_id)
        stats["current_stage"] = "failed"
        _update_run_stats(
            run_id, stats, status="failed", error=f"{type(exc).__name__}: {exc}"
        )
        return stats


def start_pipeline_run(
    db: Session,
    *,
    action: Literal["full", "crawl_only", "ai_only"],
    max_items: int,
    source_ids: list[int] | None = None,
    include_pending: bool = False,
    triggered_by: Literal["manual", "scheduler"] = "manual",
    spawn_thread: bool = True,
) -> TaskRun:
    """创建 pipeline_run 任务并可选启动后台守护线程异步执行（带防重入锁）。"""
    with _MANUAL_LOCK:
        running = db.execute(
            select(TaskRun.id)
            .where(
                TaskRun.task_type == "pipeline_run",
                TaskRun.status == "running",
            )
            .limit(1)
        ).first()
        if running is not None:
            raise AppError(
                "PIPELINE_ALREADY_RUNNING",
                f"当前已有任务 #{running[0]} 正在执行，请等待本轮完成后再触发",
                status_code=409,
                detail={"running_task_id": int(running[0])},
            )

        run = TaskRun(
            task_type="pipeline_run",
            source_id=None,
            status="running",
            triggered_by=triggered_by,
            started_at=datetime.now(timezone.utc),
            stats_json={
                "action": action,
                "max_items": max_items,
                "current_stage": "queued",
            },
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    if spawn_thread:
        t = threading.Thread(
            target=execute_pipeline_run,
            kwargs={
                "run_id": run.id,
                "action": action,
                "max_items": max_items,
                "source_ids": source_ids,
                "include_pending": include_pending,
            },
            name=f"pipeline-run-{run.id}",
            daemon=True,
        )
        t.start()

    return run
