"""信源管理端点 S1—S11 + V1/V2 + C1（03 §8.2）。

硬约束：

* 写操作全部 ``Depends(require_admin)`` 并写 ``review_action``。
* **API 进程绝不发外部 HTTP**：``POST /{id}/poll`` 只插一条 ``task_run``。
* 列表 / 详情响应全部是明确 Pydantic model。
* 静态路径（``/summary``、``/validate``、``/validate-one``、``/recalc-levels``）
  必须注册在 ``/{source_id}`` 之前，否则会被路径参数吃掉。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import Integer, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, require_admin, write_review_action
from app.core.db import get_db
from app.core.secrets import encrypt_secret, mask_secret
from app.core.errors import (
    INVALID_TYPE_FIELD,
    MISSING_EXPECTED_DOMAINS,
    READONLY_FIELD,
    RECALC_IN_PROGRESS,
    SOURCE_CODE_DUPLICATE,
    SOURCE_HAS_DATA,
    BUILTIN_SOURCE,
    AppError,
    source_not_found,
)
from app.models.pipeline import Article, RawItem
from app.models.source import Source, UnknownDomain
from app.models.task import TaskRun
from app.schemas.source import (
    CollectedItem,
    Level,
    PollResponse,
    RecalcResponse,
    RUNTIME_FIELDS,
    RunItem,
    SortBy,
    SourceCreate,
    SourceCreateResponse,
    SourceDetail,
    SourceListItem,
    SourceListResponse,
    SourceStats,
    SourceSummary,
    SourceThresholds,
    SourceType,
    SourceUpdate,
    TaskRunListResponse,
    ToggleRequest,
    ValidateRequest,
    ValidateResponse,
    ValidateResult,
    Warning,
)
from app.services import validate as validate_service
from app.services.adapters import build_google_news_url
from app.services.fetch import check_safe_outbound_url
from app.services.health import STATUS_SEVERITY, thresholds
from app.services.validate import probe_many

router = APIRouter(
    prefix="/api/sources",
    tags=["sources"],
    dependencies=[Depends(require_admin)],
)

# 03 §7.1：这四类状态需要人处理
NEEDS_ATTENTION_STATUSES: tuple[str, ...] = ("down", "auth_error", "degraded", "starved")

# 03 §3.2：人可写字段（+ last_validated_at 只由探活写）
HUMAN_WRITABLE_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "type",
    "level",
    "url",
    "query_template",
    "poll_interval_min",
    "enabled",
    "expected_domains",
    "title_suffix_rules",
    "extra_headers",
    "api_key_env",
    "note",
)

# SourceDetail 的 30 个存储字段
SOURCE_FIELDS: tuple[str, ...] = (
    "id",
    "code",
    "name",
    "type",
    "level",
    "url",
    "query_template",
    "poll_interval_min",
    "enabled",
    "expected_domains",
    "title_suffix_rules",
    "extra_headers",
    "api_key_env",
    "api_key_mask",
    "note",
    "last_validated_at",
    "etag",
    "last_modified",
    "last_poll_at",
    "last_success_at",
    "consecutive_failures",
    "success_count",
    "error_count",
    "health_status",
    "last_error",
    "last_error_at",
    "last_error_kind",
    "effective_ratio_7d",
    "expected_status_on_first_run",
    "created_at",
    "updated_at",
)

# 03 §8.4 C1 / S10 的 review_action 动作名
ACTION_CREATE = "create_source"
ACTION_UPDATE = "update_source"
ACTION_TOGGLE = "toggle_source"
ACTION_DELETE = "delete_source"
ACTION_POLL = "manual_poll"
ACTION_RESET_HEALTH = "reset_health"
ACTION_VALIDATE = "validate_sources"
ACTION_RECALC = "recalc_levels"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_source(db: Session, source_id: int) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise source_not_found(source_id)
    return source


def _as_value(value: Any) -> Any:
    """枚举 → 字符串，便于写库。"""
    return value.value if isinstance(value, Enum) else value


def _validate_type_fields(
    *,
    source_type: str,
    level: str,
    url: str | None,
    query_template: str | None,
    expected_domains: list[str] | None,
) -> None:
    """S6 / S7 共用的字段校验（03 §4 + §8.4）。"""
    domains = [d for d in (expected_domains or []) if (d or "").strip()]
    if level == "A" and not domains:
        raise AppError(
            MISSING_EXPECTED_DOMAINS,
            "A 级源必须填写 expected_domains，否则定级无法判定出处",
            status_code=400,
            detail={"field": "expected_domains"},
        )
    if source_type == "rss" and query_template:
        raise AppError(
            INVALID_TYPE_FIELD,
            "rss 类型不接受 query_template（仅 google_news 使用）",
            status_code=400,
            detail={"field": "query_template"},
        )
    if (url or "").strip():
        unsafe = check_safe_outbound_url(str(url))
        if unsafe is not None:
            raise AppError(
                INVALID_TYPE_FIELD,
                f"信源 URL 安全校验未通过：{unsafe}",
                status_code=400,
                detail={"field": "url"},
            )
    if source_type == "google_news" and not (url or "").strip() and not (
        query_template or ""
    ).strip():
        raise AppError(
            INVALID_TYPE_FIELD,
            "google_news 类型必须提供 url 或 query_template",
            status_code=400,
            detail={"fields": ["url", "query_template"]},
        )


def _aggregate_7d(db: Session, source_ids: list[int]) -> dict[int, dict[str, Any]]:
    """一条 SQL 聚合近 7 天 task_run，避免 N+1（03 §7.2）。

    口径：成功率 = 成功**任务数** / 总任务数，不是 HTTP 200 次数。
    """
    if not source_ids:
        return {}
    since = _now() - timedelta(days=7)
    fetched_expr = func.coalesce(
        cast(TaskRun.stats_json.op("->>")("fetched"), Integer), 0
    )
    duration_expr = cast(TaskRun.stats_json.op("->>")("duration_ms"), Integer)
    rows = db.execute(
        select(
            TaskRun.source_id,
            func.count().label("total"),
            func.sum(case((TaskRun.status == "success", 1), else_=0)).label("ok"),
            func.sum(case((TaskRun.status == "success", fetched_expr), else_=0)).label(
                "items"
            ),
            func.sum(
                case(
                    ((TaskRun.status == "success") & (fetched_expr == 0), 1),
                    else_=0,
                )
            ).label("empty_runs"),
            func.avg(duration_expr).label("avg_ms"),
            func.max(TaskRun.finished_at).label("last_run_at"),
        )
        .where(
            TaskRun.source_id.in_(source_ids),
            TaskRun.task_type == "poll",
            TaskRun.created_at_utc >= since,
        )
        .group_by(TaskRun.source_id)
    ).all()

    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        total = int(row.total or 0)
        ok = int(row.ok or 0)
        result[int(row.source_id)] = {
            "total": total,
            "success_rate_7d": round(ok / total, 4) if total else None,
            "items_7d": int(row.items or 0),
            "empty_runs_7d": int(row.empty_runs or 0),
            "avg_duration_ms_7d": int(round(row.avg_ms)) if row.avg_ms is not None else None,
            "last_run_at": row.last_run_at,
        }
    return result


def _p95_duration_7d(db: Session, source_id: int) -> int | None:
    """P95 耗时：7 天运行记录量级很小（≤336 条），在 Python 侧算更省事。"""
    since = _now() - timedelta(days=7)
    duration_expr = cast(TaskRun.stats_json.op("->>")("duration_ms"), Integer)
    values = [
        int(v)
        for v in db.execute(
            select(duration_expr).where(
                TaskRun.source_id == source_id,
                TaskRun.task_type == "poll",
                TaskRun.created_at_utc >= since,
            )
        )
        .scalars()
        .all()
        if v is not None
    ]
    if not values:
        return None
    values.sort()
    index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return values[index]


def _build_stats(source: Source, stats: dict[str, Any], p95: int | None) -> SourceStats:
    return SourceStats(
        success_rate_7d=stats.get("success_rate_7d"),
        items_7d=int(stats.get("items_7d") or 0),
        effective_ratio_7d=source.effective_ratio_7d,
        empty_runs_7d=int(stats.get("empty_runs_7d") or 0),
        avg_duration_ms_7d=stats.get("avg_duration_ms_7d"),
        p95_duration_ms_7d=p95,
        last_run_at=stats.get("last_run_at"),
    )


def _to_list_item(source: Source, stats: dict[str, Any]) -> SourceListItem:
    item = SourceListItem.model_validate(source)
    item.key_configured = bool(source.api_key_encrypted) or (
        bool(source.api_key_env) and bool(os.environ.get(source.api_key_env or ""))
    )
    item.success_rate_7d = stats.get("success_rate_7d")
    item.avg_duration_ms_7d = stats.get("avg_duration_ms_7d")
    item.items_7d = int(stats.get("items_7d") or 0)
    item.needs_attention = source.health_status in NEEDS_ATTENTION_STATUSES
    return item


def _to_detail(
    source: Source, stats: dict[str, Any], p95: int | None = None
) -> SourceDetail:
    data: dict[str, Any] = {name: getattr(source, name, None) for name in SOURCE_FIELDS}
    data["key_configured"] = bool(source.api_key_encrypted) or (
        bool(source.api_key_env) and bool(os.environ.get(source.api_key_env or ""))
    )
    data["etag_cached"] = bool(source.etag or source.last_modified)
    data["thresholds"] = SourceThresholds(**thresholds())
    data["stats"] = _build_stats(source, stats, p95)
    return SourceDetail.model_validate(data)


def _snapshot(source: Source) -> dict[str, Any]:
    """review_action.before_json 用：只记人可写字段，避免把运行态写进审计。"""
    data: dict[str, Any] = {}
    for name in HUMAN_WRITABLE_FIELDS:
        value = getattr(source, name, None)
        data[name] = _as_value(value)
    return data


# --------------------------------------------------------------------------- #
# S1 列表
# --------------------------------------------------------------------------- #
_ASC_SORTS = {
    SortBy.code.value: Source.code.asc(),
    SortBy.level.value: Source.level.asc(),
    SortBy.last_success_at.value: Source.last_success_at.asc().nullsfirst(),
}


def _default_order() -> tuple:
    """03 §7.6 三级默认排序，替代"健康分"。"""
    severity = case(STATUS_SEVERITY, value=Source.health_status, else_=9)
    return (
        severity.asc(),
        Source.consecutive_failures.desc(),
        Source.last_success_at.asc().nullsfirst(),
        Source.id.asc(),
    )


@router.get("", response_model=SourceListResponse)
def list_sources(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_type: SourceType | None = Query(None, alias="type"),
    level: Level | None = Query(None),
    health_status: list[str] | None = Query(None),
    enabled: bool | None = Query(None),
    keyword: str | None = Query(None),
    needs_attention: bool | None = Query(None),
    never_validated: bool | None = Query(None),
    sort_by: str | None = Query(None),
) -> SourceListResponse:
    conditions = []
    if source_type is not None:
        conditions.append(Source.type == source_type.value)
    if level is not None:
        conditions.append(Source.level == level.value)
    if health_status:
        conditions.append(Source.health_status.in_(health_status))
    if enabled is not None:
        conditions.append(Source.enabled.is_(bool(enabled)))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append(or_(Source.code.ilike(pattern), Source.name.ilike(pattern)))
    if needs_attention is not None:
        if needs_attention:
            conditions.append(Source.health_status.in_(NEEDS_ATTENTION_STATUSES))
        else:
            conditions.append(Source.health_status.not_in(NEEDS_ATTENTION_STATUSES))
    if never_validated is not None:
        if never_validated:
            conditions.append(Source.last_validated_at.is_(None))
        else:
            conditions.append(Source.last_validated_at.is_not(None))

    total = int(
        db.execute(select(func.count()).select_from(Source).where(*conditions)).scalar_one()
        or 0
    )

    offset = (page - 1) * page_size
    # sort_by 只允许固定几个键，其余一律回落默认排序（03 §8.4 S1）
    sort_key = sort_by if sort_by in {item.value for item in SortBy} else None

    if sort_key in (SortBy.success_rate_7d.value, SortBy.items_7d.value):
        # 这两个指标来自聚合结果，SQL 里排不了：先取全集算完再切片
        all_rows = db.execute(select(Source).where(*conditions)).scalars().all()
        stats_all = _aggregate_7d(db, [row.id for row in all_rows])

        def _key(item: Source) -> tuple:
            stats = stats_all.get(item.id, {})
            if sort_key == SortBy.items_7d.value:
                return (-int(stats.get("items_7d") or 0), item.id)
            rate = stats.get("success_rate_7d")
            return (-(rate if rate is not None else -1.0), item.id)

        ordered = sorted(all_rows, key=_key)
        rows = ordered[offset : offset + page_size]
    else:
        order = (
            (_ASC_SORTS[sort_key], Source.id.asc())
            if sort_key in _ASC_SORTS
            else _default_order()
        )
        rows = (
            db.execute(
                select(Source)
                .where(*conditions)
                .order_by(*order)
                .offset(offset)
                .limit(page_size)
            )
            .scalars()
            .all()
        )

    stats_map = _aggregate_7d(db, [row.id for row in rows])
    items = [_to_list_item(row, stats_map.get(row.id, {})) for row in rows]
    return SourceListResponse(items=items, total=total, page=page, page_size=page_size)


# --------------------------------------------------------------------------- #
# S2 顶部 KPI（必须注册在 /{source_id} 之前）
# --------------------------------------------------------------------------- #
@router.get("/summary", response_model=SourceSummary)
def get_summary(db: Session = Depends(get_db)) -> SourceSummary:
    counts = {
        status: int(count)
        for status, count in db.execute(
            select(Source.health_status, func.count()).group_by(Source.health_status)
        ).all()
    }
    total = sum(counts.values())
    ok_count = counts.get("ok", 0)
    needs_attention_count = sum(counts.get(status, 0) for status in NEEDS_ATTENTION_STATUSES)
    disabled_count = int(
        db.execute(
            select(func.count()).select_from(Source).where(Source.enabled.is_(False))
        ).scalar_one()
        or 0
    )
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_items = int(
        db.execute(
            select(func.count())
            .select_from(RawItem)
            .where(RawItem.fetched_at >= today_start)
        ).scalar_one()
        or 0
    )
    last_round_at = db.execute(select(func.max(TaskRun.finished_at))).scalar_one_or_none()
    unknown_domain_count = int(
        db.execute(select(func.count()).select_from(UnknownDomain)).scalar_one() or 0
    )
    return SourceSummary(
        total=total,
        ok_count=ok_count,
        needs_attention_count=needs_attention_count,
        disabled_count=disabled_count,
        today_items=today_items,
        last_round_at=last_round_at,
        unknown_domain_count=unknown_domain_count,
    )


# --------------------------------------------------------------------------- #
# V1 全量探活（同步；源不可用不算接口错误）
# --------------------------------------------------------------------------- #
@router.post("/validate", response_model=ValidateResponse)
def validate_all(
    db: Session = Depends(get_db), admin: str = current_admin
) -> ValidateResponse:
    sources = (
        db.execute(select(Source).where(Source.enabled.is_(True)).order_by(Source.id))
        .scalars()
        .all()
    )
    payloads = [
        {
            "source_type": source.type,
            "url": source.url,
            "query_template": source.query_template,
            "extra_headers": source.extra_headers or {},
            "api_key_env": source.api_key_env,
            "source_id": source.id,
            "code": source.code,
        }
        for source in sources
    ]
    raw_results = probe_many(payloads, concurrency=4)

    now = _now()
    for source in sources:
        # 探活只写 last_validated_at，不写任何业务数据（03 §8.4 V1）
        source.last_validated_at = now
    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_VALIDATE,
        after={"probed": len(payloads)},
        note="全量探活",
    )
    db.commit()

    results = [ValidateResult.model_validate(item) for item in raw_results]
    ok_count = sum(1 for result in results if result.ok)
    return ValidateResponse(
        results=results, ok_count=ok_count, fail_count=len(results) - ok_count
    )


# --------------------------------------------------------------------------- #
# V2 单源 / 未保存配置探活（不写库，除 last_validated_at）
# --------------------------------------------------------------------------- #
@router.post("/validate-one", response_model=ValidateResult)
def validate_one(
    payload: ValidateRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> ValidateResult:
    source_type = _as_value(payload.source_type)
    if (payload.url or "").strip():
        unsafe = check_safe_outbound_url(str(payload.url))
        if unsafe is not None:
            raise AppError(
                INVALID_TYPE_FIELD,
                f"探活 URL 安全校验未通过：{unsafe}",
                status_code=400,
                detail={"field": "url"},
            )
    raw = validate_service.probe_source(
        source_type=source_type,
        url=payload.url,
        query_template=payload.query_template,
        extra_headers=payload.extra_headers or {},
        api_key_env=payload.api_key_env,
        source_id=payload.source_id,
        code=None,
    )
    result = ValidateResult.model_validate(raw)

    if payload.source_id is not None:
        source = _get_source(db, payload.source_id)
        source.last_validated_at = _now()
        result.code = result.code or source.code
        write_review_action(
            db,
            reviewer=admin,
            action=ACTION_VALIDATE,
            source_id=source.id,
            after={"ok": result.ok, "entries": result.entries},
            note="单源探活",
        )
        db.commit()
    return result


# --------------------------------------------------------------------------- #
# C1 重算等级（异步任务）
# --------------------------------------------------------------------------- #
@router.post("/recalc-levels", response_model=RecalcResponse)
def recalc_levels(
    db: Session = Depends(get_db), admin: str = current_admin
) -> RecalcResponse:
    busy = db.execute(
        select(TaskRun.id).where(
            TaskRun.task_type == "recalc_levels",
            TaskRun.status.in_(("pending", "running")),
        )
    ).first()
    if busy is not None:
        raise AppError(
            RECALC_IN_PROGRESS,
            "已有重算任务在进行中，请等待其完成",
            status_code=409,
            detail={"task_run_id": busy[0]},
        )
    run = TaskRun(
        task_type="recalc_levels",
        source_id=None,
        status="pending",
        triggered_by="manual",
    )
    db.add(run)
    db.flush()
    write_review_action(
        db, reviewer=admin, action=ACTION_RECALC, note="手动触发重算等级"
    )
    db.commit()
    return RecalcResponse(task_run_id=run.id, status="pending")


# --------------------------------------------------------------------------- #
# S6 新建
# --------------------------------------------------------------------------- #
@router.post("", response_model=SourceCreateResponse, status_code=201)
def create_source(
    payload: SourceCreate, db: Session = Depends(get_db), admin: str = current_admin
) -> SourceCreateResponse:
    duplicate = db.execute(
        select(Source.id).where(Source.code == payload.code)
    ).first()
    if duplicate is not None:
        raise AppError(
            SOURCE_CODE_DUPLICATE,
            f"信源 code 已存在：{payload.code}",
            status_code=409,
            detail={"code": payload.code},
        )

    source_type = _as_value(payload.type)
    level = _as_value(payload.level)
    _validate_type_fields(
        source_type=source_type,
        level=level,
        url=payload.url,
        query_template=payload.query_template,
        expected_domains=payload.expected_domains,
    )

    url = (payload.url or "").strip() or None
    query_template = (payload.query_template or "").strip() or None
    if source_type == "google_news" and url is None and query_template:
        # 04 §5.3：query 存 query_template，url 存拼好的完整地址
        url = build_google_news_url(query_template)

    source = Source(
        code=payload.code.strip(),
        name=payload.name.strip(),
        type=source_type,
        level=level,
        url=url,
        query_template=query_template,
        poll_interval_min=payload.poll_interval_min,
        enabled=payload.enabled,
        expected_domains=list(payload.expected_domains or []),
        title_suffix_rules=list(payload.title_suffix_rules or []),
        extra_headers=dict(payload.extra_headers or {}),
        api_key_env=(payload.api_key_env or "").strip() or None,
        api_key_encrypted=encrypt_secret(payload.api_key),
        api_key_mask=mask_secret(payload.api_key),
        note=payload.note,
        # 新建不探活：last_validated_at 保持 null（03 §8.4 S6）
        last_validated_at=None,
    )
    db.add(source)
    db.flush()
    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_CREATE,
        source_id=source.id,
        after=_snapshot(source),
    )
    db.commit()
    db.refresh(source)

    detail = _to_detail(source, {}, None)
    return SourceCreateResponse.model_validate(
        {
            **detail.model_dump(),
            "warning": Warning(
                code="NOT_VALIDATED",
                message="该源尚未验证过，可能不可用。建议先点探活确认。",
            ),
        }
    )


# --------------------------------------------------------------------------- #
# S3 详情
# --------------------------------------------------------------------------- #
@router.get("/{source_id}", response_model=SourceDetail)
def get_source(source_id: int, db: Session = Depends(get_db)) -> SourceDetail:
    source = _get_source(db, source_id)
    stats = _aggregate_7d(db, [source.id]).get(source.id, {})
    return _to_detail(source, stats, _p95_duration_7d(db, source.id))


# --------------------------------------------------------------------------- #
# S4 最近运行记录
# --------------------------------------------------------------------------- #
@router.get("/{source_id}/runs", response_model=TaskRunListResponse)
def list_runs(
    source_id: int,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> TaskRunListResponse:
    _get_source(db, source_id)
    total = int(
        db.execute(
            select(func.count())
            .select_from(TaskRun)
            .where(TaskRun.source_id == source_id)
        ).scalar_one()
        or 0
    )
    rows = (
        db.execute(
            select(TaskRun)
            .where(TaskRun.source_id == source_id)
            .order_by(TaskRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = []
    for run in rows:
        stats = run.stats_json or {}
        items.append(
            RunItem(
                id=run.id,
                started_at=run.started_at,
                finished_at=run.finished_at,
                status=run.status,
                fetched=int(stats.get("fetched") or 0),
                duration_ms=stats.get("duration_ms"),
                attempts=stats.get("attempts"),
                error_kind=stats.get("error_kind"),
                error=run.error,
            )
        )
    return TaskRunListResponse(items=items, total=total, page=page, page_size=page_size)


# --------------------------------------------------------------------------- #
# S5 最新采回条目
# --------------------------------------------------------------------------- #
@router.get("/{source_id}/items", response_model=list[CollectedItem])
def list_items(
    source_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(5, ge=1, le=100),
) -> list[CollectedItem]:
    _get_source(db, source_id)
    rows = db.execute(
        select(RawItem, Article)
        .outerjoin(Article, Article.raw_item_id == RawItem.id)
        .where(RawItem.source_id == source_id)
        .order_by(RawItem.id.desc())
        .limit(limit)
    ).all()
    items: list[CollectedItem] = []
    for raw, article in rows:
        items.append(
            CollectedItem(
                raw_item_id=raw.id,
                raw_title=raw.raw_title,
                fetched_at=raw.fetched_at,
                article_id=article.id if article else None,
                source_level=article.source_level if article else None,
                source_level_reason=article.source_level_reason if article else None,
                is_event=article.is_event if article else None,
                event_type=article.event_type if article else None,
                status=article.status if article else None,
                reject_reason=article.reject_reason if article else None,
            )
        )
    return items


# --------------------------------------------------------------------------- #
# S7 改配置
# --------------------------------------------------------------------------- #
@router.patch("/{source_id}", response_model=SourceDetail)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> SourceDetail:
    source = _get_source(db, source_id)

    # 03 §3.2：收到运行态字段必须显式报错，绝不静默忽略
    rejected = sorted(set(payload.model_fields_set) & RUNTIME_FIELDS)
    if rejected:
        raise AppError(
            READONLY_FIELD,
            "以下字段由采集侧维护，后台不能修改：" + "、".join(rejected),
            status_code=400,
            detail={"rejected_fields": rejected},
        )

    changes = {
        key: _as_value(value)
        for key, value in payload.model_dump(exclude_unset=True).items()
        if key not in RUNTIME_FIELDS
    }
    if not changes:
        return _to_detail(source, _aggregate_7d(db, [source.id]).get(source.id, {}), _p95_duration_7d(db, source.id))

    if "code" in changes and changes["code"] != source.code:
        duplicate = db.execute(
            select(Source.id).where(Source.code == changes["code"], Source.id != source.id)
        ).first()
        if duplicate is not None:
            raise AppError(
                SOURCE_CODE_DUPLICATE,
                f"信源 code 已存在：{changes['code']}",
                status_code=409,
                detail={"code": changes["code"]},
            )

    merged_type = str(changes.get("type", source.type))
    merged_level = str(changes.get("level", source.level))
    merged_url = changes.get("url", source.url)
    merged_query = changes.get("query_template", source.query_template)
    merged_domains = changes.get("expected_domains", source.expected_domains)
    _validate_type_fields(
        source_type=merged_type,
        level=merged_level,
        url=merged_url,
        query_template=merged_query,
        expected_domains=merged_domains,
    )

    if merged_type == "google_news" and not (merged_url or "").strip() and (
        merged_query or ""
    ).strip():
        changes["url"] = build_google_news_url(str(merged_query))

    before = _snapshot(source)
    raw_api_key = changes.pop("api_key", None)
    clear_api_key = bool(changes.pop("clear_api_key", False))
    for key, value in changes.items():
        if key == "expected_domains":
            value = list(value or [])
        elif key == "title_suffix_rules":
            value = list(value or [])
        elif key == "extra_headers":
            value = dict(value or {})
        elif key in ("url", "query_template", "api_key_env") and isinstance(value, str):
            value = value.strip() or None
        setattr(source, key, value)

    if clear_api_key:
        source.api_key_encrypted = None
        source.api_key_mask = None
    elif raw_api_key and str(raw_api_key).strip():
        cleaned_key = str(raw_api_key).strip()
        source.api_key_encrypted = encrypt_secret(cleaned_key)
        source.api_key_mask = mask_secret(cleaned_key)

    if changes.get("enabled") is False and source.health_status != "disabled":
        # 通过 PATCH 停用与 toggle 语义一致（03 §8.4 S8）
        source.health_status = "disabled"
    elif changes.get("enabled") is True and source.health_status == "disabled":
        source.health_status = "degraded"

    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_UPDATE,
        source_id=source.id,
        before=before,
        after=_snapshot(source),
    )
    db.commit()
    db.refresh(source)
    stats = _aggregate_7d(db, [source.id]).get(source.id, {})
    return _to_detail(source, stats, _p95_duration_7d(db, source.id))


# --------------------------------------------------------------------------- #
# S8 启停
# --------------------------------------------------------------------------- #
@router.post("/{source_id}/toggle", response_model=SourceDetail)
def toggle_source(
    source_id: int,
    payload: ToggleRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> SourceDetail:
    source = _get_source(db, source_id)
    before = {"enabled": source.enabled, "health_status": source.health_status}
    source.enabled = payload.enabled
    if payload.enabled:
        # 03 §7.7 第 3 条：重新启用不直接给 ok
        if source.health_status == "disabled":
            source.health_status = "degraded"
    else:
        # 停用时的 disabled 是"人决定"的结果，但保留 consecutive_failures
        source.health_status = "disabled"
    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_TOGGLE,
        source_id=source.id,
        before=before,
        after={"enabled": source.enabled, "health_status": source.health_status},
    )
    db.commit()
    db.refresh(source)
    stats = _aggregate_7d(db, [source.id]).get(source.id, {})
    return _to_detail(source, stats, _p95_duration_7d(db, source.id))


# --------------------------------------------------------------------------- #
# S9 删除
# --------------------------------------------------------------------------- #
@router.delete("/{source_id}", status_code=204, response_class=Response)
def delete_source(
    source_id: int, db: Session = Depends(get_db), admin: str = current_admin
) -> Response:
    source = _get_source(db, source_id)

    raw_item_count = int(
        db.execute(
            select(func.count())
            .select_from(RawItem)
            .where(RawItem.source_id == source.id)
        ).scalar_one()
        or 0
    )
    if raw_item_count > 0:
        raise AppError(
            SOURCE_HAS_DATA,
            f"该源已有 {raw_item_count} 条采回数据，删除会让历史新闻来源断链",
            status_code=409,
            detail={"raw_item_count": raw_item_count, "suggestion": "请改用停用"},
        )
    if source.code == "manual":
        raise AppError(
            BUILTIN_SOURCE,
            "内置源 manual 不可删除（手工导入需要它作为归属）",
            status_code=409,
            detail={"code": "manual"},
        )

    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_DELETE,
        source_id=source.id,
        before=_snapshot(source),
    )
    db.delete(source)
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# S10 立即采集（只插任务行，绝不发 HTTP）
# --------------------------------------------------------------------------- #
@router.post("/{source_id}/poll", response_model=PollResponse)
def poll_source_now(
    source_id: int, db: Session = Depends(get_db), admin: str = current_admin
) -> PollResponse:
    source = _get_source(db, source_id)
    if not source.enabled:
        raise AppError(
            "SOURCE_DISABLED",
            "该源已停用，请先启用再采集",
            status_code=400,
            detail={"source_id": source.id},
        )

    busy = db.execute(
        select(TaskRun)
        .where(
            TaskRun.source_id == source.id,
            TaskRun.status.in_(("pending", "running")),
        )
        .order_by(TaskRun.id.desc())
    ).scalars().first()
    if busy is not None:
        # 已有的在队任务直接复用（与 tick 的 NOT EXISTS 幂等口径一致）
        return PollResponse(task_run_id=busy.id, status=busy.status)

    run = TaskRun(
        task_type="poll",
        source_id=source.id,
        status="pending",
        triggered_by="manual",
    )
    db.add(run)
    db.flush()
    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_POLL,
        source_id=source.id,
        after={"task_run_id": run.id},
        note="立即采集",
    )
    db.commit()
    return PollResponse(task_run_id=run.id, status="pending")


# --------------------------------------------------------------------------- #
# S11 重置健康度
# --------------------------------------------------------------------------- #
@router.post("/{source_id}/reset-health", response_model=SourceDetail)
def reset_health(
    source_id: int, db: Session = Depends(get_db), admin: str = current_admin
) -> SourceDetail:
    source = _get_source(db, source_id)
    before = {
        "consecutive_failures": source.consecutive_failures,
        "health_status": source.health_status,
        "last_error": source.last_error,
        "last_error_at": _as_value(source.last_error_at),
        "last_error_kind": source.last_error_kind,
    }
    # 03 §7.4：清失败计数与 last_error*，保留 success_count / error_count
    source.consecutive_failures = 0
    source.last_error = None
    source.last_error_at = None
    source.last_error_kind = None
    source.health_status = "ok"
    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_RESET_HEALTH,
        source_id=source.id,
        before=before,
        after={"consecutive_failures": 0, "health_status": "ok"},
    )
    db.commit()
    db.refresh(source)
    stats = _aggregate_7d(db, [source.id]).get(source.id, {})
    return _to_detail(source, stats, _p95_duration_7d(db, source.id))
