"""关键词规则管理 API（硬排除 / 软扣分 / 锚定词 / 信号词 / 事件动词）。"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, write_review_action
from app.core.db import get_db
from app.core.errors import VALIDATION_FAILED, AppError
from app.models.keyword import KeywordRule
from app.schemas.article import (
    KeywordRuleCreate,
    KeywordRuleListResponse,
    KeywordRuleOut,
    KeywordRuleUpdate,
)

router = APIRouter(prefix="/api/keyword-rules", tags=["keyword-rules"])


@router.get("", response_model=KeywordRuleListResponse)
def list_keyword_rules(
    db: Session = Depends(get_db),
    admin: str = current_admin,
    type: str | None = Query(None),
    enabled: bool | None = Query(None),
) -> KeywordRuleListResponse:
    del admin
    stmt = select(KeywordRule)
    if type:
        stmt = stmt.where(KeywordRule.type == type)
    if enabled is not None:
        stmt = stmt.where(KeywordRule.enabled.is_(bool(enabled)))
    rows = db.execute(stmt.order_by(KeywordRule.type.asc(), KeywordRule.id.asc())).scalars().all()
    return KeywordRuleListResponse(
        items=[KeywordRuleOut.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.post("", response_model=KeywordRuleOut, status_code=201)
def create_keyword_rule(
    payload: KeywordRuleCreate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> KeywordRuleOut:
    try:
        re.compile(payload.pattern, re.I)
    except re.error as exc:
        raise AppError(VALIDATION_FAILED, f"正则表达式不合法：{exc}", status_code=400)

    rule = KeywordRule(
        type=payload.type,
        category=payload.category.strip(),
        pattern=payload.pattern.strip(),
        weight=payload.weight,
        scope=payload.scope,
        enabled=payload.enabled,
        note=payload.note,
    )
    db.add(rule)
    db.flush()
    write_review_action(
        db,
        reviewer=admin,
        action="create_keyword_rule",
        after={"id": rule.id, "type": rule.type, "category": rule.category, "pattern": rule.pattern},
    )
    db.commit()
    db.refresh(rule)
    return KeywordRuleOut.model_validate(rule)


@router.patch("/{rule_id}", response_model=KeywordRuleOut)
def update_keyword_rule(
    rule_id: int,
    payload: KeywordRuleUpdate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> KeywordRuleOut:
    rule = db.get(KeywordRule, rule_id)
    if rule is None:
        raise AppError("KEYWORD_RULE_NOT_FOUND", f"关键词规则 {rule_id} 不存在", status_code=404)

    before = {"pattern": rule.pattern, "weight": rule.weight, "enabled": rule.enabled, "note": rule.note}
    changes = payload.model_dump(exclude_unset=True)
    if "pattern" in changes and changes["pattern"] is not None:
        try:
            re.compile(changes["pattern"], re.I)
        except re.error as exc:
            raise AppError(VALIDATION_FAILED, f"正则表达式不合法：{exc}", status_code=400)
        rule.pattern = changes["pattern"].strip()
    if "weight" in changes and changes["weight"] is not None:
        rule.weight = int(changes["weight"])
    if "enabled" in changes and changes["enabled"] is not None:
        rule.enabled = bool(changes["enabled"])
    if "note" in changes:
        rule.note = changes["note"]

    write_review_action(
        db,
        reviewer=admin,
        action="update_keyword_rule",
        before=before,
        after={"pattern": rule.pattern, "weight": rule.weight, "enabled": rule.enabled},
    )
    db.commit()
    db.refresh(rule)
    return KeywordRuleOut.model_validate(rule)


@router.delete("/{rule_id}", status_code=204, response_class=Response)
def delete_keyword_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> Response:
    rule = db.get(KeywordRule, rule_id)
    if rule is None:
        raise AppError("KEYWORD_RULE_NOT_FOUND", f"关键词规则 {rule_id} 不存在", status_code=404)

    before = {
        "id": rule.id,
        "type": rule.type,
        "category": rule.category,
        "pattern": rule.pattern,
        "weight": rule.weight,
        "enabled": rule.enabled,
    }
    write_review_action(
        db,
        reviewer=admin,
        action="delete_keyword_rule",
        before=before,
    )
    db.delete(rule)
    db.commit()
    return Response(status_code=204)
