"""健康状态回写。阈值硬编码（03 §7.1），计数按任务级（§7.3）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source
from app.models.task import TaskRun

THRESH_DEGRADED = 3
THRESH_DOWN = 10
THRESH_STARVED = 3
ERROR_TRUNCATE = 2000
STATUS_SEVERITY = {
    "down": 0,
    "auth_error": 1,
    "degraded": 2,
    "starved": 3,
    "ok": 4,
    "disabled": 5,
}


def thresholds() -> dict[str, int]:
    """前端直接显示，不在前端硬编码（03 §7.1）。"""
    return {
        "degraded": THRESH_DEGRADED,
        "down": THRESH_DOWN,
        "starved": THRESH_STARVED,
    }


def consecutive_empty_runs(session: Session, source_id: int, limit: int = THRESH_STARVED) -> bool:
    """最近 limit 次 poll 任务是否全部"成功但 0 条"。用 task_run 现算，不加字段。"""
    rows = session.execute(
        select(TaskRun)
        .where(
            TaskRun.source_id == source_id,
            TaskRun.task_type == "poll",
            TaskRun.status.in_(("success", "failed")),
        )
        .order_by(TaskRun.id.desc())
        .limit(limit)
    ).scalars().all()
    if len(rows) < limit:
        return False
    for row in rows:
        if row.status != "success":
            return False
        if int((row.stats_json or {}).get("fetched") or 0) > 0:
            return False
    return True


def mark_success(source: Source, *, fetched: int, now: datetime, empties: bool) -> None:
    source.success_count += 1
    source.consecutive_failures = 0
    source.last_success_at = now
    if fetched > 0:
        source.health_status = "ok"
        source.last_error = None
        source.last_error_at = None
        source.last_error_kind = None
        return
    # 成功但 0 条：empty_feed 与故障分开显示（03 §2.4 第 5 条）
    source.last_error = "返回 0 条。feed 可能是空壳页，或该源近期确实无更新"
    source.last_error_at = now
    source.last_error_kind = "empty_feed"
    if empties and source.health_status != "disabled":
        source.health_status = "starved"
    elif source.health_status not in ("starved", "disabled"):
        source.health_status = "ok"


def mark_failure(
    source: Source, *, error_kind: str | None, error: str | None, now: datetime
) -> None:
    source.error_count += 1
    source.last_error = (error or "")[:ERROR_TRUNCATE] or None
    source.last_error_at = now
    source.last_error_kind = error_kind

    if error_kind == "auth":
        # 401/403 从任意运行态进 auth_error，只能人工重置（03 §7.7 第 1 条）
        source.health_status = "auth_error"
        return

    source.consecutive_failures += 1
    # starved 不豁免失败累计（03 §7.7 第 2 条）
    if source.consecutive_failures >= THRESH_DOWN:
        source.health_status = "down"
    elif source.consecutive_failures >= THRESH_DEGRADED:
        source.health_status = "degraded"


def apply_poll_result(
    session: Session,
    source: Source,
    *,
    ok: bool,
    error_kind: str | None,
    error: str | None,
    fetched: int,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    source.last_poll_at = now
    if source.health_status == "disabled":
        return  # enabled=false 的源不应被调度，这里只做兜底
    if ok:
        mark_success(
            source,
            fetched=fetched,
            now=now,
            empties=consecutive_empty_runs(session, source.id),
        )
    else:
        mark_failure(source, error_kind=error_kind, error=error, now=now)


def is_stale(source: Source, now: datetime | None = None) -> bool:
    """新鲜度：超过 3 倍轮询间隔没成功（03 §7.2 第 ④ 类）。"""
    if not source.last_success_at:
        return True
    now = now or datetime.now(timezone.utc)
    interval = timedelta(minutes=max(1, source.poll_interval_min))
    return (now - source.last_success_at) > interval * 3
