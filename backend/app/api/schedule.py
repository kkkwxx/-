"""采集与 AI 调度控制 API（全部需管理员登录 + 审计留痕）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, require_admin, write_review_action
from app.core.db import get_db
from app.models.pipeline import Article
from app.models.task import TaskRun
from app.schemas.schedule import (
    RunNowRequest,
    RunNowResponse,
    ScheduleStateOut,
    ScheduleTaskRunItem,
    ScheduleTaskRunListResponse,
    ScheduleUpdateRequest,
)
from app.services.scheduler_control import (
    evaluate_schedule,
    get_or_create_schedule,
    normalize_fixed_times,
    normalize_weekdays,
    start_pipeline_run,
    validate_hhmm,
)

router = APIRouter(
    prefix="/api/schedule",
    tags=["schedule"],
    dependencies=[Depends(require_admin)],
)


def _build_state_out(db: Session) -> ScheduleStateOut:
    cfg = get_or_create_schedule(db)
    ev = evaluate_schedule(cfg)

    running = (
        db.execute(
            select(TaskRun)
            .where(
                TaskRun.task_type == "pipeline_run",
                TaskRun.status == "running",
            )
            .order_by(TaskRun.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )

    count_rows = db.execute(
        select(Article.status, func.count()).group_by(Article.status)
    ).all()
    q_counts = {str(st): int(cnt) for st, cnt in count_rows}

    return ScheduleStateOut(
        mode=cfg.mode,  # type: ignore[arg-type]
        crawl_enabled=bool(cfg.crawl_enabled),
        ai_enabled=bool(cfg.ai_enabled),
        timezone=cfg.timezone,
        weekdays=[int(d) for d in (cfg.weekdays or [])],
        window_start=cfg.window_start,
        window_end=cfg.window_end,
        fixed_times=[str(t) for t in (cfg.fixed_times or [])],
        max_ai_items_per_run=int(cfg.max_ai_items_per_run),
        last_fixed_slot_key=cfg.last_fixed_slot_key,
        updated_by=cfg.updated_by,
        updated_at=cfg.updated_at,
        server_local_time=ev.local_now_str,
        crawl_active_now=ev.crawl_active_now,
        ai_active_now=ev.ai_active_now,
        status_summary=ev.status_summary,
        next_run_hint=ev.next_run_hint,
        is_running_now=running is not None,
        running_task_id=running.id if running is not None else None,
        running_stage=(
            str((running.stats_json or {}).get("current_stage") or "running")
            if running is not None
            else None
        ),
        queue_counts=q_counts,
    )


@router.get("", response_model=ScheduleStateOut)
def get_schedule(db: Session = Depends(get_db)) -> ScheduleStateOut:
    return _build_state_out(db)


@router.put("", response_model=ScheduleStateOut)
def update_schedule(
    payload: ScheduleUpdateRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> ScheduleStateOut:
    cfg = get_or_create_schedule(db)
    before = {
        "mode": cfg.mode,
        "crawl_enabled": cfg.crawl_enabled,
        "ai_enabled": cfg.ai_enabled,
        "weekdays": cfg.weekdays,
        "window_start": cfg.window_start,
        "window_end": cfg.window_end,
        "fixed_times": cfg.fixed_times,
        "max_ai_items_per_run": cfg.max_ai_items_per_run,
    }
    changes = payload.model_dump(exclude_unset=True)

    if "mode" in changes and changes["mode"] is not None:
        cfg.mode = str(changes["mode"])
    if "crawl_enabled" in changes and changes["crawl_enabled"] is not None:
        cfg.crawl_enabled = bool(changes["crawl_enabled"])
    if "ai_enabled" in changes and changes["ai_enabled"] is not None:
        cfg.ai_enabled = bool(changes["ai_enabled"])
    if "timezone" in changes and changes["timezone"] is not None:
        cfg.timezone = str(changes["timezone"]).strip()
    if "weekdays" in changes and changes["weekdays"] is not None:
        cfg.weekdays = normalize_weekdays(changes["weekdays"])
    if "window_start" in changes and changes["window_start"] is not None:
        cfg.window_start = validate_hhmm(changes["window_start"], "window_start")
    if "window_end" in changes and changes["window_end"] is not None:
        cfg.window_end = validate_hhmm(changes["window_end"], "window_end")
    if "fixed_times" in changes and changes["fixed_times"] is not None:
        cfg.fixed_times = normalize_fixed_times(changes["fixed_times"])
    if "max_ai_items_per_run" in changes and changes["max_ai_items_per_run"] is not None:
        cfg.max_ai_items_per_run = int(changes["max_ai_items_per_run"])

    cfg.updated_by = admin
    write_review_action(
        db,
        reviewer=admin,
        action="update_pipeline_schedule",
        before=before,
        after={
            "mode": cfg.mode,
            "crawl_enabled": cfg.crawl_enabled,
            "ai_enabled": cfg.ai_enabled,
            "weekdays": cfg.weekdays,
            "window_start": cfg.window_start,
            "window_end": cfg.window_end,
            "fixed_times": cfg.fixed_times,
            "max_ai_items_per_run": cfg.max_ai_items_per_run,
        },
    )
    db.commit()
    db.refresh(cfg)
    return _build_state_out(db)


@router.post("/run-now", response_model=RunNowResponse, status_code=202)
def run_pipeline_now(
    payload: RunNowRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> RunNowResponse:
    run = start_pipeline_run(
        db,
        action=payload.action,
        max_items=payload.max_items,
        source_ids=payload.source_ids,
        include_pending=payload.include_pending,
        triggered_by="manual",
        spawn_thread=True,
    )
    write_review_action(
        db,
        reviewer=admin,
        action="trigger_pipeline_now",
        after={
            "task_run_id": run.id,
            "action": payload.action,
            "max_items": payload.max_items,
            "include_pending": payload.include_pending,
        },
    )
    db.commit()

    action_label = {
        "full": f"全链路获取（采新闻 + AI 限 {payload.max_items} 条）",
        "crawl_only": "仅采集新闻与补正文（0 AI Token）",
        "ai_only": f"仅 AI 判定与生成（限 {payload.max_items} 条）",
    }[payload.action]

    return RunNowResponse(
        task_run_id=run.id,
        status="running",
        action=payload.action,
        max_items=payload.max_items,
        message=f"已启动任务 #{run.id}：{action_label}",
    )


@router.get("/runs", response_model=ScheduleTaskRunListResponse)
def list_schedule_runs(
    db: Session = Depends(get_db),
    task_type: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
) -> ScheduleTaskRunListResponse:
    stmt = select(TaskRun)
    count_stmt = select(func.count()).select_from(TaskRun)
    if task_type:
        stmt = stmt.where(TaskRun.task_type == task_type)
        count_stmt = count_stmt.where(TaskRun.task_type == task_type)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = (
        db.execute(stmt.order_by(TaskRun.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return ScheduleTaskRunListResponse(
        items=[ScheduleTaskRunItem.model_validate(r) for r in rows],
        total=total,
    )
