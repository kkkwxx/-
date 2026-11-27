"""手工导入（04 §12）。

走**与自动采集完全相同**的定级流程：构造 ``RawItemDraft`` → ``collector.persist_items``
→ 落 ``raw_item`` + ``article(status='ingested')`` + ``full_text_status='pending'``。
不开任何后门：定级、过滤、``is_event``、人工审核照样走。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, write_review_action
from app.core.db import get_db
from app.core.errors import VALIDATION_FAILED, AppError
from app.models.pipeline import Article, RawItem
from app.models.source import Source
from app.schemas.source import ManualImportRequest, ManualImportResponse
from app.services.collector import item_url_hash, persist_items
from app.services.dto import FetchResult, RawItemDraft
from app.services.fetch import check_safe_outbound_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/raw-items", tags=["raw-items"])

ACTION_MANUAL_IMPORT = "manual_import"

RAW_ITEM_DUPLICATE = "RAW_ITEM_DUPLICATE"


@router.post("", response_model=ManualImportResponse, status_code=201)
def manual_import(
    payload: ManualImportRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> ManualImportResponse:
    url = (payload.url or "").strip()
    if not url:
        raise AppError(VALIDATION_FAILED, "url 必填", status_code=400, detail={"field": "url"})
    unsafe_reason = check_safe_outbound_url(url)
    if unsafe_reason is not None:
        raise AppError(
            VALIDATION_FAILED,
            f"URL 安全校验未通过：{unsafe_reason}",
            status_code=400,
            detail={"field": "url"},
        )

    source_code = (payload.source_code or "manual").strip() or "manual"
    source = db.execute(
        select(Source).where(Source.code == source_code)
    ).scalar_one_or_none()
    if source is None:
        raise AppError(
            VALIDATION_FAILED,
            f"找不到 code='{source_code}' 的信源，无法确定归属",
            status_code=400,
            detail={"source_code": source_code},
        )

    now = datetime.now(timezone.utc)
    pub_at = payload.published_at
    if pub_at is not None and pub_at.tzinfo is None:
        pub_at = pub_at.replace(tzinfo=timezone.utc)
    draft = RawItemDraft(
        source_id=source.id,
        url=url,
        title=(payload.title or "").strip(),
        fetched_at=now,
        resolved_url=url,
        summary=payload.summary,
        published_at=pub_at,
        extra={"manual_import": True},
    )
    result = FetchResult(items=[draft], attempts=0, duration_ms=0, stats={"fetched": 1})
    stats = persist_items(db, source, result, now=now)

    if int(stats.get("inserted") or 0) == 0:
        db.rollback()
        if int(stats.get("skipped_old") or 0) > 0:
            raise AppError(
                VALIDATION_FAILED,
                "发布时间超出时效窗口，未导入",
                status_code=400,
                detail={"stats": stats},
            )
        raise AppError(
            RAW_ITEM_DUPLICATE,
            "该 URL 已经采集/导入过，未重复写入",
            status_code=409,
            detail={"url": url, "stats": stats},
        )

    url_hash = item_url_hash(draft)
    raw = db.execute(
        select(RawItem).where(
            RawItem.source_id == source.id, RawItem.url_hash == url_hash
        )
    ).scalar_one_or_none()
    if raw is None:
        db.rollback()
        raise AppError(
            VALIDATION_FAILED, "导入后未能定位 raw_item", status_code=500
        )
    article = db.execute(
        select(Article).where(Article.raw_item_id == raw.id)
    ).scalar_one_or_none()
    if article is None:
        db.rollback()
        raise AppError(
            VALIDATION_FAILED, "导入后未能定位 article", status_code=500
        )

    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_MANUAL_IMPORT,
        source_id=source.id,
        article_id=article.id,
        after={"raw_item_id": raw.id, "url": url},
        note=payload.note,
    )
    db.commit()

    return ManualImportResponse(
        raw_item_id=raw.id,
        article_id=article.id,
        source_level=article.source_level,
        source_level_reason=article.source_level_reason,
    )
