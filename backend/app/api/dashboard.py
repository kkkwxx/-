"""运营监控 Dashboard 总览 API（采集漏斗、审核池积压、Token 预算、信源告警）。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin
from app.core.config import settings
from app.core.db import get_db
from app.models.pipeline import Article
from app.models.publish import PublishedArticle
from app.models.source import Source, UnknownDomain
from app.schemas.article import DashboardOverview, SourceAlertItem
from app.services.ai import get_today_tokens

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverview)
def dashboard_overview(
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> DashboardOverview:
    del admin
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    status_rows = db.execute(
        select(Article.status, func.count()).group_by(Article.status)
    ).all()
    pipeline_counts = {str(st): int(cnt) for st, cnt in status_rows}

    today_ingested = int(
        db.execute(
            select(func.count()).select_from(Article).where(Article.created_at >= today_start)
        ).scalar_one()
        or 0
    )
    today_published = int(
        db.execute(
            select(func.count())
            .select_from(PublishedArticle)
            .where(PublishedArticle.published_at >= today_start)
        ).scalar_one()
        or 0
    )
    active_published_total = int(
        db.execute(
            select(func.count())
            .select_from(PublishedArticle)
            .where(PublishedArticle.is_active.is_(True))
        ).scalar_one()
        or 0
    )

    today_tokens = get_today_tokens(db)
    budget = max(1, int(settings.LLM_DAILY_TOKEN_BUDGET or 200_000))
    ratio = round(min(1.0, today_tokens / budget), 4)

    health_rows = db.execute(
        select(Source.health_status, func.count()).group_by(Source.health_status)
    ).all()
    source_health_counts = {str(st): int(cnt) for st, cnt in health_rows}

    unknown_domain_count = int(
        db.execute(select(func.count()).select_from(UnknownDomain)).scalar_one() or 0
    )

    alerts = db.execute(
        select(Source)
        .where(
            Source.enabled.is_(True),
            Source.health_status.in_(("down", "auth_error", "degraded", "starved")),
        )
        .order_by(Source.consecutive_failures.desc(), Source.id.asc())
        .limit(10)
    ).scalars().all()

    ev_rows = db.execute(
        select(Article.event_type, func.count())
        .where(Article.event_type.is_not(None))
        .group_by(Article.event_type)
    ).all()
    event_type_counts = {str(et): int(cnt) for et, cnt in ev_rows}

    return DashboardOverview(
        pipeline_counts=pipeline_counts,
        today_ingested=today_ingested,
        today_published=today_published,
        active_published_total=active_published_total,
        today_tokens=today_tokens,
        daily_token_budget=budget,
        token_usage_ratio=ratio,
        source_health_counts=source_health_counts,
        unknown_domain_count=unknown_domain_count,
        alert_sources=[
            SourceAlertItem(
                id=s.id,
                code=s.code,
                name=s.name,
                health_status=s.health_status,
                consecutive_failures=s.consecutive_failures,
                last_error_kind=s.last_error_kind,
                last_error=s.last_error,
            )
            for s in alerts
        ],
        event_type_counts=event_type_counts,
    )
