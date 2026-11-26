"""域名规则端点 R1—R5（03 §8.2 组 4）。

R2 的语义：域名归一 → 查重 → 建规则 → **同事务删除 unknown_domain 同域名记录**
→ 返回 ``recalc_suggested: true``（改规则后历史文章等级需要重算，见 C1）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, write_review_action
from app.core.db import get_db
from app.core.errors import (
    DOMAIN_EXISTS,
    INVALID_DOMAIN,
    AppError,
)
from app.models.source import SourceDomainRule, UnknownDomain
from app.schemas.source import (
    DomainRuleCreate,
    DomainRuleCreateResponse,
    DomainRuleItem,
    DomainRuleListResponse,
    DomainRuleUpdate,
    UnknownDomainItem,
    UnknownDomainListResponse,
)
from app.services.domain import is_valid_registrable_domain, registrable_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/source-domain-rules", tags=["domain-rules"])

ACTION_CREATE_RULE = "create_domain_rule"
ACTION_UPDATE_RULE = "update_domain_rule"
ACTION_DELETE_RULE = "delete_domain_rule"

# 暂不可用（已与需求方确认）：03 §8.3 要 hit_count_30d（近 30 天该规则命中数）。
# 表结构里没有计数列；靠 article.source_level_reason 的 'ruleN:domain' 做 LIKE
# 统计既不准（rule2—rule6 都算命中）又要扫 article 大表。统一返回 None，
# 前端显示「—」。要真实口径得加字段或加统计任务，本期不做。
HIT_COUNT_30D_UNAVAILABLE: int | None = None


def _get_rule(db: Session, rule_id: int) -> SourceDomainRule:
    rule = db.get(SourceDomainRule, rule_id)
    if rule is None:
        raise AppError(
            "DOMAIN_RULE_NOT_FOUND",
            f"域名规则 {rule_id} 不存在",
            status_code=404,
            detail={"rule_id": rule_id},
        )
    return rule


def _to_item(rule: SourceDomainRule) -> DomainRuleItem:
    item = DomainRuleItem.model_validate(rule)
    item.hit_count_30d = HIT_COUNT_30D_UNAVAILABLE
    return item


# --------------------------------------------------------------------------- #
# R4 未收录域名报表（先注册，避免被 /{rule_id} 吃掉）
# --------------------------------------------------------------------------- #
@router.get("/unknown", response_model=UnknownDomainListResponse)
def list_unknown_domains(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> UnknownDomainListResponse:
    total = int(
        db.execute(select(func.count()).select_from(UnknownDomain)).scalar_one() or 0
    )
    rows = (
        db.execute(
            select(UnknownDomain)
            .order_by(UnknownDomain.hit_count.desc(), UnknownDomain.domain.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return UnknownDomainListResponse(
        items=[UnknownDomainItem.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# --------------------------------------------------------------------------- #
# R1 规则列表
# --------------------------------------------------------------------------- #
@router.get("", response_model=DomainRuleListResponse)
def list_domain_rules(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    level: str | None = Query(None),
    enabled: bool | None = Query(None),
    keyword: str | None = Query(None),
) -> DomainRuleListResponse:
    conditions: list[Any] = []
    if level:
        conditions.append(SourceDomainRule.level == level)
    if enabled is not None:
        conditions.append(SourceDomainRule.enabled.is_(bool(enabled)))
    if keyword:
        conditions.append(SourceDomainRule.domain.ilike(f"%{keyword.strip()}%"))

    total = int(
        db.execute(
            select(func.count()).select_from(SourceDomainRule).where(*conditions)
        ).scalar_one()
        or 0
    )
    rows = (
        db.execute(
            select(SourceDomainRule)
            .where(*conditions)
            .order_by(SourceDomainRule.domain.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return DomainRuleListResponse(
        items=[_to_item(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# --------------------------------------------------------------------------- #
# R2 新增规则
# --------------------------------------------------------------------------- #
@router.post("", response_model=DomainRuleCreateResponse, status_code=201)
def create_domain_rule(
    payload: DomainRuleCreate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> DomainRuleCreateResponse:
    raw = (payload.domain or "").strip()
    if not is_valid_registrable_domain(raw):
        raise AppError(
            INVALID_DOMAIN,
            f"「{payload.domain}」不是合法的注册域（需要 eTLD+1，如 nasa.gov）",
            status_code=400,
            detail={"domain": payload.domain},
        )
    # 域名必须先归一（小写 eTLD+1），否则 notnasa.gov 会被当成 nasa.gov
    domain = registrable_domain(raw) or raw.lower()

    existing = db.execute(
        select(SourceDomainRule).where(SourceDomainRule.domain == domain)
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            DOMAIN_EXISTS,
            f"域名规则已存在：{domain}",
            status_code=409,
            detail={"domain": domain, "rule_id": existing.id},
        )

    rule = SourceDomainRule(
        domain=domain,
        level=payload.level.value,
        note=payload.note,
        enabled=True,
    )
    db.add(rule)
    db.flush()

    # 同事务清理未收录记录：运营已经归类，报表里不该再有它（03 §8.4 R2）
    unknown = db.execute(
        select(UnknownDomain).where(UnknownDomain.domain == domain)
    ).scalar_one_or_none()
    if unknown is not None:
        db.delete(unknown)

    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_CREATE_RULE,
        after={"domain": domain, "level": payload.level.value, "note": payload.note},
    )
    db.commit()
    db.refresh(rule)

    item = _to_item(rule)
    return DomainRuleCreateResponse.model_validate(
        {**item.model_dump(), "recalc_suggested": True}
    )


# --------------------------------------------------------------------------- #
# R3 改等级 / 备注 / 启停
# --------------------------------------------------------------------------- #
@router.patch("/{rule_id}", response_model=DomainRuleItem)
def update_domain_rule(
    rule_id: int,
    payload: DomainRuleUpdate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> DomainRuleItem:
    rule = _get_rule(db, rule_id)
    before = {"level": rule.level, "note": rule.note, "enabled": rule.enabled}
    changes = payload.model_dump(exclude_unset=True)
    if "level" in changes and changes["level"] is not None:
        rule.level = getattr(changes["level"], "value", changes["level"])
    if "note" in changes:
        rule.note = changes["note"]
    if "enabled" in changes and changes["enabled"] is not None:
        rule.enabled = bool(changes["enabled"])

    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_UPDATE_RULE,
        before=before,
        after={"level": rule.level, "note": rule.note, "enabled": rule.enabled},
    )
    db.commit()
    db.refresh(rule)
    return _to_item(rule)


# --------------------------------------------------------------------------- #
# R5 软删除
# --------------------------------------------------------------------------- #
@router.delete("/{rule_id}", status_code=204, response_class=Response)
def delete_domain_rule(
    rule_id: int, db: Session = Depends(get_db), admin: str = current_admin
) -> Response:
    rule = _get_rule(db, rule_id)
    before = {"level": rule.level, "enabled": rule.enabled}
    # 软删除：直接删会让历史 reason 指向不存在的规则，无法回溯
    rule.enabled = False
    write_review_action(
        db,
        reviewer=admin,
        action=ACTION_DELETE_RULE,
        before=before,
        after={"enabled": False},
        note="软删除（enabled=false）",
    )
    db.commit()
    return Response(status_code=204)
