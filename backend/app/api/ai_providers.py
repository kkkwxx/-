"""AI API 提供商管理端点（全部需管理员登录 + 审计留痕，严禁回传明文 Key）。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, require_admin, write_review_action
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import VALIDATION_FAILED, AppError
from app.core.secrets import encrypt_secret, mask_secret
from app.models.api_provider import AiProviderConfig
from app.schemas.api_provider import (
    AiProviderCreate,
    AiProviderListResponse,
    AiProviderOut,
    AiProviderPresetOut,
    AiProviderRoleRequest,
    AiProviderTestResponse,
    AiProviderUpdate,
)
from app.services.ai_providers import (
    PROVIDER_PRESETS,
    ensure_default_provider_seeded,
    get_provider_today_tokens,
    test_provider_connectivity,
    validate_provider_base_url,
)

router = APIRouter(
    prefix="/api/ai-providers",
    tags=["ai-providers"],
    dependencies=[Depends(require_admin)],
)


def _to_out(db: Session, row: AiProviderConfig) -> AiProviderOut:
    out = AiProviderOut.model_validate(row)
    return out.model_copy(
        update={
            "has_api_key": bool(row.api_key_encrypted),
            "today_tokens": get_provider_today_tokens(db, row),
        }
    )


def _safe_audit_snapshot(row: AiProviderConfig) -> dict:
    """审计快照仅记脱敏信息，绝不记录明文或密文 Key。"""
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "vendor": row.vendor,
        "base_url": row.base_url,
        "api_key_mask": row.api_key_mask,
        "model_classify": row.model_classify,
        "model_generate": row.model_generate,
        "daily_token_budget": row.daily_token_budget,
        "role": row.role,
        "enabled": row.enabled,
    }


def _demote_other_role_holders(
    db: Session, role: str, *, exclude_id: int | None = None
) -> None:
    """主用（primary）与备用（fallback）全站各仅允许 1 条，切新角色时旧同角色降为 standby。"""
    if role not in ("primary", "fallback"):
        return
    stmt = select(AiProviderConfig).where(AiProviderConfig.role == role)
    if exclude_id is not None:
        stmt = stmt.where(AiProviderConfig.id != exclude_id)
    for other in db.execute(stmt).scalars().all():
        other.role = "standby"


@router.get("", response_model=AiProviderListResponse)
def list_ai_providers(db: Session = Depends(get_db)) -> AiProviderListResponse:
    ensure_default_provider_seeded(db)
    rows = (
        db.execute(
            select(AiProviderConfig).order_by(AiProviderConfig.id.asc())
        )
        .scalars()
        .all()
    )
    role_order = {"primary": 0, "fallback": 1, "standby": 2}
    rows.sort(key=lambda r: (role_order.get(r.role, 9), r.id))

    env_key = (settings.DASHSCOPE_API_KEY or "").strip()
    return AiProviderListResponse(
        items=[_to_out(db, r) for r in rows],
        presets=[
            AiProviderPresetOut(
                vendor=p.vendor,  # type: ignore[arg-type]
                label=p.label,
                base_url=p.base_url,
                default_model_classify=p.default_model_classify,
                default_model_generate=p.default_model_generate,
                default_budget=p.default_budget,
                requires_key=p.requires_key,
                note=p.note,
            )
            for p in PROVIDER_PRESETS
        ],
        env_fallback_configured=bool(env_key),
        env_fallback_mask=mask_secret(env_key),
    )


@router.post("", response_model=AiProviderOut, status_code=201)
def create_ai_provider(
    payload: AiProviderCreate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> AiProviderOut:
    code = payload.code.strip()
    dup = db.execute(
        select(AiProviderConfig.id).where(AiProviderConfig.code == code)
    ).first()
    if dup is not None:
        raise AppError(
            "AI_PROVIDER_CODE_DUPLICATE",
            f"提供商编码 {code} 已存在",
            status_code=409,
        )

    url_err = validate_provider_base_url(payload.vendor, payload.base_url)
    if url_err is not None:
        raise AppError(VALIDATION_FAILED, url_err, status_code=400)

    raw_key = (payload.api_key or "").strip()
    if not raw_key and payload.vendor != "ollama":
        raise AppError(
            VALIDATION_FAILED,
            "除本地 Ollama 外，新增 AI 提供商必须提供 API Key",
            status_code=400,
        )

    if payload.role in ("primary", "fallback"):
        _demote_other_role_holders(db, payload.role)

    row = AiProviderConfig(
        code=code,
        name=payload.name.strip(),
        vendor=payload.vendor,
        base_url=payload.base_url.strip(),
        api_key_encrypted=encrypt_secret(raw_key),
        api_key_mask=mask_secret(raw_key),
        model_classify=payload.model_classify.strip(),
        model_generate=payload.model_generate.strip(),
        daily_token_budget=int(payload.daily_token_budget),
        role=payload.role,
        enabled=bool(payload.enabled),
        note=(payload.note or "").strip() or None,
    )
    db.add(row)
    db.flush()
    write_review_action(
        db,
        reviewer=admin,
        action="create_ai_provider",
        after=_safe_audit_snapshot(row),
    )
    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@router.patch("/{provider_id}", response_model=AiProviderOut)
def update_ai_provider(
    provider_id: int,
    payload: AiProviderUpdate,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> AiProviderOut:
    row = db.get(AiProviderConfig, provider_id)
    if row is None:
        raise AppError("AI_PROVIDER_NOT_FOUND", f"AI 提供商 {provider_id} 不存在", status_code=404)

    before = _safe_audit_snapshot(row)
    changes = payload.model_dump(exclude_unset=True)
    next_vendor = changes.get("vendor", row.vendor)
    next_url = changes.get("base_url", row.base_url)
    url_err = validate_provider_base_url(str(next_vendor), str(next_url))
    if url_err is not None:
        raise AppError(VALIDATION_FAILED, url_err, status_code=400)

    if "name" in changes and changes["name"] is not None:
        row.name = str(changes["name"]).strip()
    if "vendor" in changes and changes["vendor"] is not None:
        row.vendor = str(changes["vendor"])
    if "base_url" in changes and changes["base_url"] is not None:
        row.base_url = str(changes["base_url"]).strip()
    if "model_classify" in changes and changes["model_classify"] is not None:
        row.model_classify = str(changes["model_classify"]).strip()
    if "model_generate" in changes and changes["model_generate"] is not None:
        row.model_generate = str(changes["model_generate"]).strip()
    if "daily_token_budget" in changes and changes["daily_token_budget"] is not None:
        row.daily_token_budget = int(changes["daily_token_budget"])
    if "enabled" in changes and changes["enabled"] is not None:
        row.enabled = bool(changes["enabled"])
    if "note" in changes:
        row.note = (str(changes["note"]).strip() if changes["note"] is not None else None) or None

    if changes.get("clear_api_key"):
        row.api_key_encrypted = None
        row.api_key_mask = None
    elif "api_key" in changes and (changes["api_key"] or "").strip():
        new_key = str(changes["api_key"]).strip()
        row.api_key_encrypted = encrypt_secret(new_key)
        row.api_key_mask = mask_secret(new_key)

    if "role" in changes and changes["role"] is not None:
        new_role = str(changes["role"])
        if new_role in ("primary", "fallback"):
            _demote_other_role_holders(db, new_role, exclude_id=row.id)
        row.role = new_role

    write_review_action(
        db,
        reviewer=admin,
        action="update_ai_provider",
        before=before,
        after=_safe_audit_snapshot(row),
    )
    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@router.post("/{provider_id}/role", response_model=AiProviderOut)
def set_ai_provider_role(
    provider_id: int,
    payload: AiProviderRoleRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> AiProviderOut:
    row = db.get(AiProviderConfig, provider_id)
    if row is None:
        raise AppError("AI_PROVIDER_NOT_FOUND", f"AI 提供商 {provider_id} 不存在", status_code=404)

    before = _safe_audit_snapshot(row)
    if payload.role in ("primary", "fallback"):
        _demote_other_role_holders(db, payload.role, exclude_id=row.id)
        row.enabled = True
    row.role = payload.role

    write_review_action(
        db,
        reviewer=admin,
        action="switch_ai_provider_role",
        before=before,
        after=_safe_audit_snapshot(row),
    )
    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@router.post("/{provider_id}/test", response_model=AiProviderTestResponse)
def test_ai_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> AiProviderTestResponse:
    row = db.get(AiProviderConfig, provider_id)
    if row is None:
        raise AppError("AI_PROVIDER_NOT_FOUND", f"AI 提供商 {provider_id} 不存在", status_code=404)

    ok, latency_ms, method, detail = test_provider_connectivity(row)
    now = datetime.now(timezone.utc)
    row.last_test_status = "ok" if ok else "failed"
    row.last_test_at = now
    row.last_test_error = None if ok else detail[:500]
    write_review_action(
        db,
        reviewer=admin,
        action="test_ai_provider",
        after={"id": row.id, "code": row.code, "ok": ok, "method": method, "latency_ms": latency_ms},
    )
    db.commit()
    return AiProviderTestResponse(
        ok=ok,
        latency_ms=latency_ms,
        method=method,
        detail=detail,
        tested_at=now,
    )


@router.delete("/{provider_id}", status_code=204, response_class=Response)
def delete_ai_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> Response:
    row = db.get(AiProviderConfig, provider_id)
    if row is None:
        raise AppError("AI_PROVIDER_NOT_FOUND", f"AI 提供商 {provider_id} 不存在", status_code=404)

    write_review_action(
        db,
        reviewer=admin,
        action="delete_ai_provider",
        before=_safe_audit_snapshot(row),
    )
    db.delete(row)
    db.commit()
    return Response(status_code=204)
