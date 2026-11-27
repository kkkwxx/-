"""公开栏目展示 API（免管理员登录，严格只读 ``published_article`` 不可变快照）。

提供：
- ``GET /api/column/articles``：支持分类筛选、关键词搜索（标题/导语/正文/来源）、
  A/B/C 信源筛选、悬念评分门槛、以及「综合热度 / 最新时间」两种排序；
- ``GET /api/column/ranking``：右侧「神秘热榜」Top N（跨源数 + hook_score + A/B 源 + 点击量 + 时效衰减）；
- ``GET /api/column/articles/{published_id}``：读取详情快照并原子递增 ``view_count``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.models.publish import EventCluster, PublishedArticle
from app.schemas.article import (
    ColumnArticleListResponse,
    ColumnRankingItem,
    ColumnRankingResponse,
    PublishedSnapshotOut,
)
from app.services.ranking import compute_hot_score, compute_rank_badge

router = APIRouter(prefix="/api/column", tags=["column"])

RANKING_FORMULA_DESC = (
    "综合神秘热度 = (悬念评分×18 + 权威源加权 + 跨源报道数×15 + 真实浏览热度) × 时效新鲜度"
)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _enrich_snapshot(
    row: PublishedArticle,
    *,
    cluster_count: int,
    now: datetime,
    rank_index: int | None = None,
) -> PublishedSnapshotOut:
    hot_score = compute_hot_score(row, cluster_article_count=cluster_count, now=now)
    rank_badge = compute_rank_badge(
        row,
        cluster_article_count=cluster_count,
        rank_index=rank_index,
        now=now,
    )
    out = PublishedSnapshotOut.model_validate(row)
    return out.model_copy(
        update={
            "hot_score": hot_score,
            "cluster_article_count": max(int(cluster_count or 1), 1),
            "rank_badge": rank_badge,
        }
    )


def _load_active_cluster_counts(
    db: Session, cluster_ids: set[int]
) -> dict[int, int]:
    if not cluster_ids:
        return {}
    rows = db.execute(
        select(EventCluster.id, EventCluster.article_count).where(
            EventCluster.id.in_(cluster_ids)
        )
    ).all()
    return {int(cid): max(int(cnt or 1), 1) for cid, cnt in rows}


@router.get("/articles", response_model=ColumnArticleListResponse)
def list_column_articles(
    db: Session = Depends(get_db),
    event_type: str | None = Query(None),
    q: str | None = Query(None, max_length=120),
    sort: Literal["hot", "latest"] = Query("hot"),
    source_level: Literal["A", "B", "C"] | None = Query(None),
    min_hook_score: int | None = Query(None, ge=1, le=5),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> ColumnArticleListResponse:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 左侧分类计数与右侧概览（统计全站当前在展快照）
    type_rows = db.execute(
        select(PublishedArticle.event_type, func.count())
        .where(PublishedArticle.is_active.is_(True))
        .group_by(PublishedArticle.event_type)
    ).all()
    counts_by_type = {str(et): int(cnt) for et, cnt in type_rows}
    total_active = sum(counts_by_type.values())
    today_published = int(
        db.execute(
            select(func.count())
            .select_from(PublishedArticle)
            .where(
                PublishedArticle.is_active.is_(True),
                PublishedArticle.published_at >= today_start,
            )
        ).scalar_one()
        or 0
    )

    conditions: list[Any] = [PublishedArticle.is_active.is_(True)]
    if event_type:
        conditions.append(PublishedArticle.event_type == event_type)
    if source_level:
        conditions.append(PublishedArticle.source_level == source_level)
    if min_hook_score is not None:
        conditions.append(PublishedArticle.hook_score >= min_hook_score)
    keyword = (q or "").strip()
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        conditions.append(
            or_(
                PublishedArticle.title_final.ilike(pattern, escape="\\"),
                PublishedArticle.body_lead.ilike(pattern, escape="\\"),
                PublishedArticle.body_summary.ilike(pattern, escape="\\"),
                PublishedArticle.source_name.ilike(pattern, escape="\\"),
                PublishedArticle.style_tag.ilike(pattern, escape="\\"),
                PublishedArticle.culture_tag.ilike(pattern, escape="\\"),
            )
        )

    if sort == "latest":
        total = int(
            db.execute(
                select(func.count()).select_from(PublishedArticle).where(*conditions)
            ).scalar_one()
            or 0
        )
        rows = db.execute(
            select(PublishedArticle)
            .where(*conditions)
            .order_by(PublishedArticle.published_at.desc(), PublishedArticle.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        cluster_map = _load_active_cluster_counts(
            db, {r.event_cluster_id for r in rows if r.event_cluster_id is not None}
        )
        items = [
            _enrich_snapshot(
                r,
                cluster_count=cluster_map.get(r.event_cluster_id or -1, 1),
                now=now,
            )
            for r in rows
        ]
    else:
        # 综合热度排序：与右侧热榜共用同一套确定性评分公式
        all_rows = db.execute(
            select(PublishedArticle).where(*conditions)
        ).scalars().all()
        total = len(all_rows)
        cluster_map = _load_active_cluster_counts(
            db, {r.event_cluster_id for r in all_rows if r.event_cluster_id is not None}
        )
        scored: list[tuple[int, datetime, int, PublishedArticle, int]] = []
        for r in all_rows:
            c_cnt = cluster_map.get(r.event_cluster_id or -1, 1)
            score = compute_hot_score(r, cluster_article_count=c_cnt, now=now)
            scored.append((score, r.published_at, r.id, r, c_cnt))
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

        start = (page - 1) * page_size
        page_slice = scored[start : start + page_size]
        items = [
            _enrich_snapshot(
                r,
                cluster_count=c_cnt,
                now=now,
                rank_index=start + idx,
            )
            for idx, (_, _, _, r, c_cnt) in enumerate(page_slice)
        ]

    return ColumnArticleListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        counts_by_type=counts_by_type,
        total_active=total_active,
        today_published=today_published,
    )


@router.get("/ranking", response_model=ColumnRankingResponse)
def get_column_ranking(
    db: Session = Depends(get_db),
    event_type: str | None = Query(None),
    limit: int = Query(10, ge=1, le=20),
) -> ColumnRankingResponse:
    now = datetime.now(timezone.utc)
    conditions: list[Any] = [PublishedArticle.is_active.is_(True)]
    if event_type:
        conditions.append(PublishedArticle.event_type == event_type)

    rows = db.execute(
        select(PublishedArticle).where(*conditions)
    ).scalars().all()
    cluster_map = _load_active_cluster_counts(
        db, {r.event_cluster_id for r in rows if r.event_cluster_id is not None}
    )

    scored: list[tuple[int, datetime, int, PublishedArticle, int]] = []
    for r in rows:
        c_cnt = cluster_map.get(r.event_cluster_id or -1, 1)
        score = compute_hot_score(r, cluster_article_count=c_cnt, now=now)
        scored.append((score, r.published_at, r.id, r, c_cnt))
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

    ranking_items: list[ColumnRankingItem] = []
    for idx, (score, _, _, r, c_cnt) in enumerate(scored[:limit]):
        ranking_items.append(
            ColumnRankingItem(
                rank=idx + 1,
                id=r.id,
                title_final=r.title_final,
                event_type=r.event_type,
                source_name=r.source_name,
                source_level=r.source_level,
                hook_score=r.hook_score,
                view_count=int(r.view_count or 0),
                cluster_article_count=c_cnt,
                hot_score=score,
                rank_badge=compute_rank_badge(
                    r,
                    cluster_article_count=c_cnt,
                    rank_index=idx,
                    now=now,
                ),
                published_at=r.published_at,
            )
        )

    return ColumnRankingResponse(
        items=ranking_items,
        formula_desc=RANKING_FORMULA_DESC,
        updated_at=now,
    )


@router.get("/articles/{published_id}", response_model=PublishedSnapshotOut)
def get_column_article(
    published_id: int, db: Session = Depends(get_db)
) -> PublishedSnapshotOut:
    # 原子递增阅读点击量（仅对在展快照生效）
    updated = db.execute(
        update(PublishedArticle)
        .where(
            PublishedArticle.id == published_id,
            PublishedArticle.is_active.is_(True),
        )
        .values(view_count=PublishedArticle.view_count + 1)
        .returning(PublishedArticle.id)
    ).scalar_one_or_none()
    if updated is None:
        raise AppError("COLUMN_ARTICLE_NOT_FOUND", "该栏目内容不存在或已下架", status_code=404)
    db.commit()

    row = db.execute(
        select(PublishedArticle).where(PublishedArticle.id == published_id)
    ).scalar_one()
    c_cnt = 1
    if row.event_cluster_id is not None:
        cluster = db.get(EventCluster, row.event_cluster_id)
        if cluster is not None:
            c_cnt = max(int(cluster.article_count or 1), 1)
    return _enrich_snapshot(row, cluster_count=c_cnt, now=datetime.now(timezone.utc))
