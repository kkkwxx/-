"""审核工作台与内部发布快照 API（08 审核工作台 + 09 发布层）。

核心原则：
- 全人工审核：所有发布必须经管理员操作并过 7 项发布 Checklist；
- 编辑保存实时重校验：修改标题/导语/摘要即时跑确定性轻校验并刷新 risk_flags；
- 发布写不可变 ``published_article`` 快照 + ``push_record``，更新 ``event_cluster.is_published``；
- 已发布内容若需修改，必须退回重审再发新版本快照。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, write_review_action
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import VALIDATION_FAILED, AppError
from app.models.ai import AiRun
from app.models.audit import ReviewAction
from app.models.pipeline import Article, RawItem
from app.models.publish import EventCluster, PublishedArticle, PushRecord
from app.schemas.article import (
    AiRunOut,
    ArticleDetail,
    ArticleEditRequest,
    ArticleListItem,
    ArticleListResponse,
    BatchDeleteArticlesRequest,
    BatchDeleteArticlesResponse,
    BatchDeleteSkippedItem,
    ChecklistItem,
    ClusterSiblingOut,
    PublishedSnapshotOut,
    PublishRequest,
    ReviewActionOut,
    ReviewActionRequest,
    TitleCandidateOut,
    VerificationOut,
)
from app.services.fetch import check_safe_outbound_url
from app.services.policy import get_active_policy
from app.services.verifier import (
    SUPERNATURAL_NATURES,
    default_disclaimer_for,
    verify_content,
)

router = APIRouter(prefix="/api/articles", tags=["articles"])
published_router = APIRouter(prefix="/api/published", tags=["published"])

VALID_EVENT_TYPES = frozenset(
    {"deep_space_particle", "neo", "exoplanet", "bci_consciousness", "anomaly_psychic"}
)
ALLOWED_EDIT_EVENT_TYPES = VALID_EVENT_TYPES | {"other"}
ALLOWED_CONTENT_NATURES = frozenset(
    {"科学事实", "科研进展", "未证实目击", "灵异传闻", "娱乐化灵异", "伪科学主张"}
)
APPROVABLE_STATUSES = frozenset({"candidate", "pending", "approved", "unpublished"})
# 非内容类上游风险标记，在编辑保存重算轻校验时必须完整保留
UPSTREAM_RISK_FLAGS = frozenset(
    {
        "stale_event",
        "missing_publish_time",
        "no_full_text",
        "cross_verified_by_ab",
        "unverified_source",
        "distant_future_event",
        "off_topic_borderline",
        "budget_exceeded",
        "ai_generate_failed",
    }
)


def _get_article_or_404(db: Session, article_id: int) -> Article:
    art = db.get(Article, article_id)
    if art is None:
        raise AppError("ARTICLE_NOT_FOUND", f"文章 {article_id} 不存在", status_code=404)
    return art


def _build_checklist(
    db: Session,
    article: Article,
    raw: RawItem | None,
) -> tuple[list[ChecklistItem], VerificationOut, bool]:
    """服务端实时计算需求 §8 的 7 项发布 Checklist（动态读取最新风控策略，绕过缓存）。"""
    pt = get_active_policy(db, bypass_cache=True).thresholds
    verification = verify_content(
        title_final=article.title_final,
        body_lead=article.body_lead,
        body_summary=article.body_summary,
        fact_anchor=article.fact_anchor,
        event_type=article.event_type,
        content_nature=article.content_nature,
        source_level=article.source_level,
        title_min_chars=pt.title_min_chars,
        title_max_chars=pt.title_max_chars,
    )
    ver_out = VerificationOut(
        passed=verification.passed,
        hard_failed=verification.hard_failed,
        errors=verification.errors,
        warnings=verification.warnings,
        risk_flags=verification.risk_flags,
    )

    # 1. 来源等级合规（A/B 级直接过；C 级必须具备真实非空的人工补证或跨源佐证）
    has_manual_evidence = any(
        isinstance(ev, dict)
        and (
            ev.get("type") == "cross_source_match"
            or bool(str(ev.get("url") or "").strip())
            or bool(str(ev.get("note") or "").strip())
        )
        for ev in (article.evidence_notes or [])
    )
    source_ok = article.source_level in ("A", "B") or has_manual_evidence
    # 2. 事件型判定
    is_event_ok = article.is_event is True
    # 3. 命中五类内容之一
    type_ok = (article.event_type or "") in VALID_EVENT_TYPES
    # 4. 动态时效门禁：报道本身发布/发现时间必须满足 freshness_hours（对齐 dedup_freshness.py）
    now = datetime.now(timezone.utc)
    pub_t = raw.published_at if raw else None
    if pub_t is not None and pub_t.tzinfo is None:
        pub_t = pub_t.replace(tzinfo=timezone.utc)
    ref_t = pub_t or article.first_seen_at or article.created_at
    if ref_t.tzinfo is None:
        ref_t = ref_t.replace(tzinfo=timezone.utc)
    fresh_ok = article.status != "expired" and (
        now - ref_t <= timedelta(hours=int(pt.freshness_hours))
    )
    # 5. 去重防重发：仅当同事件簇下存在「其他文章（article_id != article.id）」的活跃发布快照时阻断，
    #    彻底解决同一篇文章编辑退回重审后发布 v2 新快照被自锁的问题。
    other_published = False
    if article.event_cluster_id:
        other_snap = db.execute(
            select(PublishedArticle.id)
            .where(
                PublishedArticle.event_cluster_id == article.event_cluster_id,
                PublishedArticle.article_id != article.id,
                PublishedArticle.is_active.is_(True),
            )
            .limit(1)
        ).first()
        if other_snap is not None:
            other_published = True
    dedup_ok = article.status != "duplicate" and not other_published
    # 6. 标题/正文轻校验无阻断错误（含红线词与灵异免责声明）
    content_ok = verification.passed
    # 7. 具备可追溯来源链接
    source_url = article.original_url or article.publisher_url or (raw.url if raw else "")
    url_ok = bool(source_url)

    items = [
        ChecklistItem(
            key="source_level",
            label="来源可核验（A/B 级，或 C 级已完成人工补证）",
            passed=source_ok,
            detail=f"当前等级 {article.source_level}（{article.source_level_reason or '无'}）",
        ),
        ChecklistItem(
            key="is_event",
            label="事件型判定通过（is_event=true）",
            passed=is_event_ok,
            detail="真实具体事件" if is_event_ok else "未确认为具体事件",
        ),
        ChecklistItem(
            key="event_type",
            label="命中五大核心栏目之一",
            passed=type_ok,
            detail=article.event_type or "未归类",
        ),
        ChecklistItem(
            key="freshness",
            label=f"满足 {pt.freshness_hours} 小时推送时效（含未来事件）",
            passed=fresh_ok,
            detail="未来预告事件" if article.is_future_event else "时效正常" if fresh_ok else f"已超 {pt.freshness_hours}h 时效",
        ),
        ChecklistItem(
            key="dedup",
            label="跨源去重通过且所属事件簇未重复发布",
            passed=dedup_ok,
            detail="未重复" if dedup_ok else "同事件已存在发布记录",
        ),
        ChecklistItem(
            key="content_verify",
            label="标题与正文轻校验通过（无红线词、灵异带限定语）",
            passed=content_ok,
            detail="校验通过" if content_ok else "；".join(verification.errors),
        ),
        ChecklistItem(
            key="source_url",
            label="保留原始来源链接",
            passed=url_ok,
            detail=source_url[:120] if url_ok else "缺少来源链接",
        ),
    ]
    can_pub = all(i.passed for i in items) and article.status in ("approved", "unpublished")
    return items, ver_out, can_pub


def _to_list_item(article: Article, raw: RawItem | None) -> ArticleListItem:
    return ArticleListItem(
        id=article.id,
        title=article.title,
        title_final=article.title_final,
        body_lead=article.body_lead,
        publisher_name=article.publisher_name,
        source_level=article.source_level,
        source_level_reason=article.source_level_reason,
        event_type=article.event_type,
        content_nature=article.content_nature,
        is_event=article.is_event,
        event_confidence=(
            float(article.event_confidence) if article.event_confidence is not None else None
        ),
        hook_score=article.hook_score,
        style_tag=article.style_tag,
        culture_tag=article.culture_tag,
        review_priority=article.review_priority,
        risk_flags=list(article.risk_flags or []),
        status=article.status,
        reject_reason=article.reject_reason,
        need_review=article.need_review,
        event_cluster_id=article.event_cluster_id,
        published_at_raw=raw.published_at if raw else None,
        fetched_at=raw.fetched_at if raw else None,
        event_time=article.event_time,
        first_seen_at=article.first_seen_at,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


def _to_detail(db: Session, article: Article) -> ArticleDetail:
    raw = db.get(RawItem, article.raw_item_id)
    base = _to_list_item(article, raw)
    checklist, ver_out, can_pub = _build_checklist(db, article, raw)

    siblings: list[ClusterSiblingOut] = []
    if article.event_cluster_id:
        sib_rows = db.execute(
            select(Article)
            .where(
                Article.event_cluster_id == article.event_cluster_id,
                Article.id != article.id,
            )
            .order_by(Article.id.asc())
            .limit(10)
        ).scalars().all()
        siblings = [
            ClusterSiblingOut(
                id=s.id,
                title=s.title,
                source_level=s.source_level,
                publisher_name=s.publisher_name,
                original_url=s.original_url or s.publisher_url,
                status=s.status,
                created_at=s.created_at,
            )
            for s in sib_rows
        ]

    ai_runs = db.execute(
        select(AiRun)
        .where(AiRun.article_id == article.id)
        .order_by(AiRun.id.desc())
        .limit(10)
    ).scalars().all()
    actions = db.execute(
        select(ReviewAction)
        .where(ReviewAction.article_id == article.id)
        .order_by(ReviewAction.id.desc())
        .limit(20)
    ).scalars().all()
    latest_pub = db.execute(
        select(PublishedArticle)
        .where(PublishedArticle.article_id == article.id)
        .order_by(PublishedArticle.version.desc(), PublishedArticle.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    cands = [
        TitleCandidateOut.model_validate(c)
        for c in (article.title_candidates or [])
        if isinstance(c, dict) and c.get("title")
    ]

    return ArticleDetail(
        **base.model_dump(),
        summary=article.summary,
        raw_title=raw.raw_title if raw else None,
        raw_summary=raw.raw_summary if raw else None,
        full_text=raw.full_text if raw else None,
        full_text_status=raw.full_text_status if raw else None,
        raw_url=raw.url if raw else None,
        original_url=article.original_url,
        publisher_url=article.publisher_url,
        resolve_status=article.resolve_status,
        entities=list(article.entities or []),
        event_action=article.event_action,
        is_future_event=article.is_future_event,
        fact_anchor=article.fact_anchor,
        title_candidates=cands,
        body_summary=article.body_summary,
        evidence_notes=list(article.evidence_notes or []),
        ai_generated_at=article.ai_generated_at,
        verification=ver_out,
        publish_checklist=checklist,
        can_publish=can_pub,
        cluster_siblings=siblings,
        ai_runs=[AiRunOut.model_validate(r) for r in ai_runs],
        review_actions=[ReviewActionOut.model_validate(a) for a in actions],
        latest_published=(
            PublishedSnapshotOut.model_validate(latest_pub) if latest_pub else None
        ),
    )


# --------------------------------------------------------------------------- #
# 审核列表与聚合详情
# --------------------------------------------------------------------------- #


@router.get("", response_model=ArticleListResponse)
def list_articles(
    db: Session = Depends(get_db),
    admin: str = current_admin,
    status: str | None = Query(None, description="逗号分隔状态过滤，如 candidate 或 rule_rejected,duplicate,expired"),
    event_type: str | None = Query(None),
    source_level: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ArticleListResponse:
    del admin
    conditions: list[Any] = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            conditions.append(Article.status == statuses[0])
        elif statuses:
            conditions.append(Article.status.in_(statuses))
    if event_type:
        conditions.append(Article.event_type == event_type)
    if source_level:
        conditions.append(Article.source_level == source_level)
    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Article.title.ilike(kw),
                Article.title_final.ilike(kw),
                Article.body_lead.ilike(kw),
                Article.publisher_name.ilike(kw),
            )
        )

    total = int(
        db.execute(select(func.count()).select_from(Article).where(*conditions)).scalar_one() or 0
    )
    rows = db.execute(
        select(Article, RawItem)
        .join(RawItem, Article.raw_item_id == RawItem.id)
        .where(*conditions)
        .order_by(Article.review_priority.desc(), Article.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    count_rows = db.execute(
        select(Article.status, func.count()).group_by(Article.status)
    ).all()
    counts_by_status = {str(st): int(cnt) for st, cnt in count_rows}

    return ArticleListResponse(
        items=[_to_list_item(art, raw) for art, raw in rows],
        total=total,
        page=page,
        page_size=page_size,
        counts_by_status=counts_by_status,
    )


def _delete_article_if_allowed(
    db: Session, art: Article, admin: str
) -> str | None:
    """安全删除单条未发布文章；若存在在线/历史发布快照则拒绝删除并返回原因。"""
    if art.status == "published":
        return "文章已发布，请先下架，且已发布历史记录不可直接物理删除"

    snap_count = int(
        db.execute(
            select(func.count())
            .select_from(PublishedArticle)
            .where(PublishedArticle.article_id == art.id)
        ).scalar_one()
        or 0
    )
    if snap_count > 0:
        return "该文章存在历史发布快照，为保留发布审计链路不可直接删除"

    cluster_id = art.event_cluster_id
    before = {
        "id": art.id,
        "title": art.title,
        "title_final": art.title_final,
        "status": art.status,
        "source_level": art.source_level,
        "event_type": art.event_type,
    }
    write_review_action(
        db,
        reviewer=admin,
        action="delete_article",
        article_id=None,
        before=before,
        note="运营手动删除文章",
    )
    db.delete(art)
    db.flush()

    if cluster_id is not None:
        cluster = db.get(EventCluster, cluster_id)
        if cluster is not None:
            remaining = db.execute(
                select(Article)
                .where(Article.event_cluster_id == cluster_id)
                .order_by(Article.id.asc())
            ).scalars().all()
            if not remaining:
                db.delete(cluster)
            else:
                cluster.article_count = len(remaining)
                if cluster.lead_article_id is None or cluster.lead_article_id == before["id"]:
                    cluster.lead_article_id = remaining[0].id
    return None


@router.post("/batch-delete", response_model=BatchDeleteArticlesResponse)
def batch_delete_articles(
    payload: BatchDeleteArticlesRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> BatchDeleteArticlesResponse:
    unique_ids = list(dict.fromkeys(payload.ids))
    arts = db.execute(
        select(Article).where(Article.id.in_(unique_ids)).with_for_update()
    ).scalars().all()
    arts_by_id = {a.id: a for a in arts}

    deleted_ids: list[int] = []
    skipped: list[BatchDeleteSkippedItem] = []

    for aid in unique_ids:
        art = arts_by_id.get(aid)
        if art is None:
            skipped.append(BatchDeleteSkippedItem(id=aid, reason="文章不存在"))
            continue
        err = _delete_article_if_allowed(db, art, admin)
        if err is not None:
            skipped.append(BatchDeleteSkippedItem(id=aid, reason=err))
        else:
            deleted_ids.append(aid)

    db.commit()
    return BatchDeleteArticlesResponse(deleted_ids=deleted_ids, skipped=skipped)


@router.delete("/{article_id}", status_code=204, response_class=Response)
def delete_article(
    article_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> Response:
    art = db.execute(
        select(Article).where(Article.id == article_id).with_for_update()
    ).scalar_one_or_none()
    if art is None:
        raise AppError("ARTICLE_NOT_FOUND", f"文章 {article_id} 不存在", status_code=404)

    err = _delete_article_if_allowed(db, art, admin)
    if err is not None:
        raise AppError(
            "ARTICLE_DELETE_FORBIDDEN",
            err,
            status_code=409,
            detail={"article_id": article_id},
        )
    db.commit()
    return Response(status_code=204)


@router.get("/{article_id}", response_model=ArticleDetail)
def get_article_detail(
    article_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> ArticleDetail:
    del admin
    art = _get_article_or_404(db, article_id)
    return _to_detail(db, art)


# --------------------------------------------------------------------------- #
# 编辑内容与实时轻校验
# --------------------------------------------------------------------------- #


@router.patch("/{article_id}", response_model=ArticleDetail)
def edit_article(
    article_id: int,
    payload: ArticleEditRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> ArticleDetail:
    art = _get_article_or_404(db, article_id)
    # 01-总体架构 §3：已发布文章修改必须退回重新审核再发布，绝不原地篡改快照
    before = {
        "title_final": art.title_final,
        "body_lead": art.body_lead,
        "body_summary": art.body_summary,
        "status": art.status,
        "culture_tag": art.culture_tag,
    }
    changes = payload.model_dump(exclude_unset=True)
    if "event_type" in changes and changes["event_type"] is not None:
        if changes["event_type"] not in ALLOWED_EDIT_EVENT_TYPES:
            raise AppError(
                VALIDATION_FAILED,
                f"不支持的栏目类型：{changes['event_type']}",
                status_code=400,
            )
    if "content_nature" in changes and changes["content_nature"] is not None:
        if changes["content_nature"] not in ALLOWED_CONTENT_NATURES:
            raise AppError(
                VALIDATION_FAILED,
                f"不支持的内容性质：{changes['content_nature']}",
                status_code=400,
            )

    for field in (
        "title_final",
        "body_lead",
        "body_summary",
        "fact_anchor",
        "style_tag",
        "hook_score",
        "event_type",
        "content_nature",
        "culture_tag",
    ):
        if field in changes:
            setattr(art, field, changes[field])

    ev_url = (changes.get("evidence_url") or "").strip()
    ev_note = (changes.get("evidence_note") or "").strip()
    if ev_url:
        unsafe_ev = check_safe_outbound_url(ev_url)
        if unsafe_ev is not None:
            raise AppError(
                VALIDATION_FAILED,
                f"佐证链接不合法：{unsafe_ev}",
                status_code=400,
                detail={"field": "evidence_url"},
            )
    if ev_url or ev_note:
        notes = list(art.evidence_notes or [])
        notes.append(
            {
                "type": "manual_evidence",
                "url": ev_url,
                "note": ev_note,
                "reviewer": admin,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        art.evidence_notes = notes

    # 若已发布/已审核文章修改了核心展示或风控属性，强制退回待审状态（绝不原地篡改快照）
    sensitive_edited = any(
        k in changes
        for k in ("title_final", "body_lead", "body_summary", "event_type", "content_nature")
    )
    if art.status in ("published", "approved") and sensitive_edited:
        art.status = (
            "pending"
            if art.event_type == "anomaly_psychic" or art.content_nature in SUPERNATURAL_NATURES
            else "candidate"
        )

    # 实时运行轻校验并同步刷新 risk_flags（完整保留非内容类上游风险标记）
    pt = get_active_policy(db, bypass_cache=True).thresholds
    ver = verify_content(
        title_final=art.title_final,
        body_lead=art.body_lead,
        body_summary=art.body_summary,
        fact_anchor=art.fact_anchor,
        event_type=art.event_type,
        content_nature=art.content_nature,
        source_level=art.source_level,
        title_min_chars=pt.title_min_chars,
        title_max_chars=pt.title_max_chars,
    )
    preserved = [f for f in (art.risk_flags or []) if f in UPSTREAM_RISK_FLAGS]
    art.risk_flags = list(dict.fromkeys([*preserved, *ver.risk_flags]))

    write_review_action(
        db,
        reviewer=admin,
        action="edit_article",
        article_id=art.id,
        before=before,
        after={
            "title_final": art.title_final,
            "body_lead": art.body_lead,
            "status": art.status,
            "culture_tag": art.culture_tag,
            "risk_flags": art.risk_flags,
        },
    )
    db.commit()
    db.refresh(art)
    return _to_detail(db, art)


# --------------------------------------------------------------------------- #
# 人工审核状态流转
# --------------------------------------------------------------------------- #


@router.post("/{article_id}/review", response_model=ArticleDetail)
def review_article(
    article_id: int,
    payload: ReviewActionRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> ArticleDetail:
    art = _get_article_or_404(db, article_id)
    before = {"status": art.status, "reject_reason": art.reject_reason}

    if payload.action == "approve":
        if art.status not in APPROVABLE_STATUSES:
            raise AppError(
                "INVALID_STATUS_TRANSITION",
                f"当前状态（{art.status}）不允许直接审核通过，请先重跑或核验来源",
                status_code=400,
            )
        pt = get_active_policy(db, bypass_cache=True).thresholds
        ver = verify_content(
            title_final=art.title_final,
            body_lead=art.body_lead,
            body_summary=art.body_summary,
            fact_anchor=art.fact_anchor,
            event_type=art.event_type,
            content_nature=art.content_nature,
            source_level=art.source_level,
            title_min_chars=pt.title_min_chars,
            title_max_chars=pt.title_max_chars,
        )
        if not ver.passed or ver.hard_failed:
            raise AppError(
                VALIDATION_FAILED,
                "内容轻校验未通过，无法审核通过：" + "；".join(ver.errors),
                status_code=400,
                detail={"errors": ver.errors},
            )
        art.status = "approved"
        art.reject_reason = None
    elif payload.action == "reject":
        if art.status == "published":
            raise AppError(
                "INVALID_STATUS_TRANSITION",
                "已发布文章请先执行下架，再做拒绝处理",
                status_code=400,
            )
        reason = (payload.reject_reason or "").strip()
        if not reason:
            raise AppError(
                VALIDATION_FAILED, "拒绝文章时必须填写 reject_reason", status_code=400
            )
        art.status = "rejected"
        art.reject_reason = reason
    elif payload.action == "to_pending":
        if art.status == "published":
            raise AppError(
                "INVALID_STATUS_TRANSITION",
                "已发布文章请先执行下架，再转入待定池",
                status_code=400,
            )
        art.status = "pending"
    elif payload.action == "re_generate":
        if art.status == "published":
            raise AppError(
                "INVALID_STATUS_TRANSITION",
                "已发布文章请先执行下架，再退回 AI 重生成",
                status_code=400,
            )
        art.status = "judged"
        art.reject_reason = None
    elif payload.action == "re_preprocess":
        if art.status == "published":
            raise AppError(
                "INVALID_STATUS_TRANSITION",
                "已发布文章请先执行下架，再退回重新预处理",
                status_code=400,
            )
        art.status = "ingested"
        art.reject_reason = None

    write_review_action(
        db,
        reviewer=admin,
        action=f"review_{payload.action}",
        article_id=art.id,
        before=before,
        after={"status": art.status, "reject_reason": art.reject_reason},
        note=payload.note,
    )
    db.commit()
    db.refresh(art)
    return _to_detail(db, art)


# --------------------------------------------------------------------------- #
# 内部发布（写不可变 published_article 快照 + push_record）
# --------------------------------------------------------------------------- #


@router.post("/{article_id}/publish", response_model=PublishedSnapshotOut, status_code=201)
def publish_article(
    article_id: int,
    payload: PublishRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> PublishedSnapshotOut:
    # 行锁防并发发布双活快照
    art = db.execute(
        select(Article).where(Article.id == article_id).with_for_update()
    ).scalar_one_or_none()
    if art is None:
        raise AppError("ARTICLE_NOT_FOUND", f"文章 {article_id} 不存在", status_code=404)

    if art.event_cluster_id:
        db.execute(
            select(EventCluster)
            .where(EventCluster.id == art.event_cluster_id)
            .with_for_update()
        ).scalar_one_or_none()

    if art.status not in ("approved", "unpublished"):
        raise AppError(
            "INVALID_STATUS",
            f"仅已审核通过（approved）或已下架（unpublished）的文章可发布，当前状态为 {art.status}",
            status_code=400,
        )

    raw = db.get(RawItem, art.raw_item_id)
    checklist, _ver, _can_pub = _build_checklist(db, art, raw)
    failed = [item for item in checklist if not item.passed]
    if failed:
        raise AppError(
            "PUBLISH_CHECKLIST_FAILED",
            "发布 Checklist 未通过：" + "；".join(f"{i.label}({i.detail})" for i in failed),
            status_code=400,
            detail={"failed_items": [i.model_dump() for i in failed]},
        )

    now = datetime.now(timezone.utc)
    # 停用该文章可能存在的旧版发布快照并递增版本号
    prev_rows = db.execute(
        select(PublishedArticle).where(PublishedArticle.article_id == art.id)
    ).scalars().all()
    next_version = max((p.version for p in prev_rows), default=0) + 1
    carried_views = max((int(p.view_count or 0) for p in prev_rows), default=0)
    for prev in prev_rows:
        prev.is_active = False

    source_url = art.original_url or art.publisher_url or (raw.url if raw else "")
    mandatory_disclaimer = default_disclaimer_for(art.event_type, art.content_nature)
    custom_disclaimer = (payload.disclaimer or "").strip()
    if mandatory_disclaimer:
        # 灵异/异常类强制保留标准免责声明，不允许被弱化覆盖
        disclaimer = (
            f"{mandatory_disclaimer} {custom_disclaimer}".strip()
            if custom_disclaimer and mandatory_disclaimer not in custom_disclaimer
            else mandatory_disclaimer
        )
    else:
        disclaimer = custom_disclaimer or None
    snapshot = PublishedArticle(
        article_id=art.id,
        event_cluster_id=art.event_cluster_id,
        version=next_version,
        title_final=(art.title_final or art.title).strip(),
        body_lead=(art.body_lead or "").strip(),
        body_summary=(art.body_summary or "").strip(),
        event_type=art.event_type or "deep_space_particle",
        source_name=art.publisher_name or "可核验媒体",
        source_url=source_url,
        source_level=art.source_level,
        content_nature=art.content_nature,
        disclaimer=disclaimer,
        style_tag=art.style_tag,
        hook_score=art.hook_score,
        culture_tag=art.culture_tag,
        view_count=carried_views,
        is_active=True,
        published_at=now,
        operator=admin,
    )
    db.add(snapshot)
    db.flush()

    db.add(
        PushRecord(
            published_article_id=snapshot.id,
            action="publish",
            channel="internal_column",
            payload_json={
                "article_id": art.id,
                "version": next_version,
                "title_final": snapshot.title_final,
                "source_level": snapshot.source_level,
            },
            status="success",
            operator=admin,
        )
    )

    art.status = "published"
    if art.event_cluster_id:
        cluster = db.get(EventCluster, art.event_cluster_id)
        if cluster is not None:
            cluster.is_published = True
            cluster.published_at = now

    write_review_action(
        db,
        reviewer=admin,
        action="publish_article",
        article_id=art.id,
        after={"published_article_id": snapshot.id, "version": next_version},
        note=payload.note,
    )
    db.commit()
    db.refresh(snapshot)
    return PublishedSnapshotOut.model_validate(snapshot)


@published_router.post("/{published_id}/unpublish", response_model=PublishedSnapshotOut)
def unpublish_snapshot(
    published_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> PublishedSnapshotOut:
    snap = db.execute(
        select(PublishedArticle).where(PublishedArticle.id == published_id).with_for_update()
    ).scalar_one_or_none()
    if snap is None:
        raise AppError("PUBLISHED_NOT_FOUND", f"发布快照 {published_id} 不存在", status_code=404)
    now = datetime.now(timezone.utc)
    snap.is_active = False
    snap.unpublished_at = now

    art = db.get(Article, snap.article_id)
    if art is not None:
        art.status = "unpublished"

    # 下架后同步校准事件簇 is_published 状态，允许同簇其他合规稿件补发
    if snap.event_cluster_id:
        cluster = db.get(EventCluster, snap.event_cluster_id)
        if cluster is not None:
            still_active = db.execute(
                select(PublishedArticle.id)
                .where(
                    PublishedArticle.event_cluster_id == snap.event_cluster_id,
                    PublishedArticle.id != snap.id,
                    PublishedArticle.is_active.is_(True),
                )
                .limit(1)
            ).first()
            cluster.is_published = still_active is not None

    db.add(
        PushRecord(
            published_article_id=snap.id,
            action="unpublish",
            channel="internal_column",
            payload_json={"published_id": snap.id},
            status="success",
            operator=admin,
        )
    )
    write_review_action(
        db,
        reviewer=admin,
        action="unpublish_article",
        article_id=snap.article_id,
        after={"published_id": snap.id, "is_active": False},
    )
    db.commit()
    db.refresh(snap)
    return PublishedSnapshotOut.model_validate(snap)


@published_router.post("/{published_id}/republish", response_model=PublishedSnapshotOut)
def republish_snapshot(
    published_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> PublishedSnapshotOut:
    snap = db.execute(
        select(PublishedArticle).where(PublishedArticle.id == published_id).with_for_update()
    ).scalar_one_or_none()
    if snap is None:
        raise AppError("PUBLISHED_NOT_FOUND", f"发布快照 {published_id} 不存在", status_code=404)

    art = db.execute(
        select(Article).where(Article.id == snap.article_id).with_for_update()
    ).scalar_one_or_none()
    if art is None or art.status != "unpublished":
        raise AppError(
            "INVALID_STATUS",
            f"仅已下架（unpublished）的文章支持重新上架，当前状态：{art.status if art else 'missing'}",
            status_code=400,
        )

    latest = db.execute(
        select(PublishedArticle)
        .where(PublishedArticle.article_id == snap.article_id)
        .order_by(PublishedArticle.version.desc(), PublishedArticle.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None or latest.id != snap.id:
        raise AppError("STALE_SNAPSHOT", "仅允许重新上架该文章的最新快照版本", status_code=400)

    raw = db.get(RawItem, art.raw_item_id)
    checklist, _ver, _can_pub = _build_checklist(db, art, raw)
    failed = [item for item in checklist if not item.passed]
    if failed:
        raise AppError(
            "PUBLISH_CHECKLIST_FAILED",
            "重新上架未通过 Checklist：" + "；".join(f"{i.label}({i.detail})" for i in failed),
            status_code=400,
        )

    now = datetime.now(timezone.utc)
    # 确保该文章其余旧快照均非活跃
    others = db.execute(
        select(PublishedArticle).where(
            PublishedArticle.article_id == snap.article_id,
            PublishedArticle.id != snap.id,
        )
    ).scalars().all()
    for o in others:
        o.is_active = False

    snap.is_active = True
    snap.unpublished_at = None
    snap.published_at = now
    art.status = "published"
    if snap.event_cluster_id:
        cluster = db.get(EventCluster, snap.event_cluster_id)
        if cluster is not None:
            cluster.is_published = True
            cluster.published_at = now

    db.add(
        PushRecord(
            published_article_id=snap.id,
            action="republish",
            channel="internal_column",
            payload_json={"published_id": snap.id},
            status="success",
            operator=admin,
        )
    )
    write_review_action(
        db,
        reviewer=admin,
        action="republish_article",
        article_id=snap.article_id,
        after={"published_id": snap.id, "is_active": True},
    )
    db.commit()
    db.refresh(snap)
    return PublishedSnapshotOut.model_validate(snap)
