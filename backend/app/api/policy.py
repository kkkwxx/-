"""风控严厉度档位开关与阈值动态配置 API（GET/PUT /api/policy）。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_admin, write_review_action
from app.core.db import get_db
from app.core.errors import VALIDATION_FAILED, AppError
from app.services.policy import PRESETS, get_active_policy, save_policy

router = APIRouter(prefix="/api/policy", tags=["policy"])


class PolicyThresholdsOut(BaseModel):
    event_confidence_min: float
    not_event_confidence_min: float
    freshness_hours: int
    dedup_trgm_threshold: float
    title_min_chars: int
    title_max_chars: int
    require_full_text_for_candidate: bool


class PolicyPresetOut(BaseModel):
    key: Literal["strict", "balanced", "relaxed"]
    label: str
    description: str
    thresholds: PolicyThresholdsOut


class RiskPolicyOut(BaseModel):
    profile: Literal["strict", "balanced", "relaxed"]
    thresholds: PolicyThresholdsOut
    overrides: dict[str, float | int | bool] = Field(default_factory=dict)
    has_custom_overrides: bool = False
    updated_by: str = "system"
    updated_at: datetime | None = None
    presets: list[PolicyPresetOut] = Field(default_factory=list)


class RiskPolicyUpdateRequest(BaseModel):
    profile: Literal["strict", "balanced", "relaxed"]
    event_confidence_min: float | None = Field(default=None, ge=0.30, le=0.99)
    not_event_confidence_min: float | None = Field(default=None, ge=0.50, le=0.99)
    freshness_hours: int | None = Field(default=None, ge=6, le=168)
    dedup_trgm_threshold: float | None = Field(default=None, ge=0.30, le=0.95)
    title_min_chars: int | None = Field(default=None, ge=4, le=15)
    title_max_chars: int | None = Field(default=None, ge=12, le=30)
    require_full_text_for_candidate: bool | None = None
    reset_overrides: bool = False


def _build_out(active) -> RiskPolicyOut:
    presets = [
        PolicyPresetOut(
            key=p.key,  # type: ignore[arg-type]
            label=p.label,
            description=p.description,
            thresholds=PolicyThresholdsOut(**asdict(p.thresholds)),
        )
        for p in PRESETS.values()
    ]
    return RiskPolicyOut(
        profile=active.profile,  # type: ignore[arg-type]
        thresholds=PolicyThresholdsOut(**asdict(active.thresholds)),
        overrides=dict(active.overrides),
        has_custom_overrides=active.has_custom_overrides,
        updated_by=active.updated_by,
        updated_at=active.updated_at,
        presets=presets,
    )


@router.get("", response_model=RiskPolicyOut)
def get_policy(
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> RiskPolicyOut:
    del admin
    return _build_out(get_active_policy(db, bypass_cache=True))


@router.put("", response_model=RiskPolicyOut)
def update_policy(
    payload: RiskPolicyUpdateRequest,
    db: Session = Depends(get_db),
    admin: str = current_admin,
) -> RiskPolicyOut:
    before_policy = get_active_policy(db, bypass_cache=True)
    before = {
        "profile": before_policy.profile,
        "thresholds": asdict(before_policy.thresholds),
    }

    override_fields = (
        "event_confidence_min",
        "not_event_confidence_min",
        "freshness_hours",
        "dedup_trgm_threshold",
        "title_min_chars",
        "title_max_chars",
        "require_full_text_for_candidate",
    )
    overrides_in = {
        k: getattr(payload, k)
        for k in override_fields
        if getattr(payload, k) is not None
    }
    # 如果只传了 profile 且没有传具体阈值，则切换到该档位预设值；
    # 若在同档位下传了部分阈值，则与该档位既有自定义覆盖增量合并（防止覆盖丢失）。
    use_reset = payload.reset_overrides or not overrides_in
    merged_overrides = dict(overrides_in)
    if not use_reset and payload.profile == before_policy.profile:
        merged_overrides = {**before_policy.overrides, **overrides_in}

    # 显式校验标题字数上下限，拒绝 min > max 的非法配置
    preset_t = PRESETS[payload.profile].thresholds
    eff_min = int(merged_overrides.get("title_min_chars", preset_t.title_min_chars))
    eff_max = int(merged_overrides.get("title_max_chars", preset_t.title_max_chars))
    if eff_min > eff_max:
        raise AppError(
            VALIDATION_FAILED,
            f"标题最小字数（{eff_min}）不能大于最大字数（{eff_max}）",
            status_code=400,
        )

    updated = save_policy(
        db,
        profile=payload.profile,
        overrides=merged_overrides,
        reset_overrides=use_reset,
        updated_by=admin,
    )
    write_review_action(
        db,
        reviewer=admin,
        action="update_risk_policy",
        before=before,
        after={"profile": updated.profile, "thresholds": asdict(updated.thresholds)},
    )
    db.commit()
    return _build_out(get_active_policy(db, bypass_cache=True))
