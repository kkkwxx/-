"""风控严厉度档位与动态阈值策略服务（M6）。

支持三档预设（``strict`` / ``balanced`` / ``relaxed``）+ 自定义覆盖（``overrides``），
通过单行表 ``risk_policy_config`` 持久化并带短 TTL 内存缓存，运行时切换无需重启服务。

安全底线不可通过任何档位关闭：
- 全人工审核（``need_review=true``）；
- 红线绝对化词硬拦截；
- C 级信源与灵异/异常类强制进入 ``pending`` 待定复核池并要求限定语。
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.policy import POLICY_PROFILES, RiskPolicyConfig

POLICY_ROW_ID = 1
_CACHE_TTL_SEC = 5.0


@dataclass(frozen=True)
class PolicyThresholds:
    event_confidence_min: float
    not_event_confidence_min: float
    freshness_hours: int
    dedup_trgm_threshold: float
    title_min_chars: int
    title_max_chars: int
    require_full_text_for_candidate: bool


@dataclass(frozen=True)
class PolicyPresetMeta:
    key: str
    label: str
    description: str
    thresholds: PolicyThresholds


PRESETS: dict[str, PolicyPresetMeta] = {
    "strict": PolicyPresetMeta(
        key="strict",
        label="严格风控",
        description="适用于正式对外推送或高合规敏感期：更高置信度门槛、24h 时效、更严去重、10—18 字标题，无全文强制进待定池。",
        thresholds=PolicyThresholds(
            event_confidence_min=0.85,
            not_event_confidence_min=0.70,
            freshness_hours=24,
            dedup_trgm_threshold=0.55,
            title_min_chars=10,
            title_max_chars=18,
            require_full_text_for_candidate=True,
        ),
    ),
    "balanced": PolicyPresetMeta(
        key="balanced",
        label="均衡默认",
        description="日常推荐基线：兼顾召回与准确度，48h 时效、7—20 字去标点标题兼容需求示例，允许摘要源进入候选池。",
        thresholds=PolicyThresholds(
            event_confidence_min=0.70,
            not_event_confidence_min=0.80,
            freshness_hours=48,
            dedup_trgm_threshold=0.65,
            title_min_chars=7,
            title_max_chars=20,
            require_full_text_for_candidate=False,
        ),
    ),
    "relaxed": PolicyPresetMeta(
        key="relaxed",
        label="宽松召回",
        description="适用于冷启动或演示期拓宽选题：放宽置信度与 72h 时效，但仍坚守全人工审核、红线词与灵异进待定池底线。",
        thresholds=PolicyThresholds(
            event_confidence_min=0.55,
            not_event_confidence_min=0.90,
            freshness_hours=72,
            dedup_trgm_threshold=0.75,
            title_min_chars=6,
            title_max_chars=22,
            require_full_text_for_candidate=False,
        ),
    ),
}

THRESHOLD_BOUNDS: dict[str, tuple[float, float]] = {
    "event_confidence_min": (0.30, 0.99),
    "not_event_confidence_min": (0.50, 0.99),
    "freshness_hours": (6, 168),
    "dedup_trgm_threshold": (0.30, 0.95),
    "title_min_chars": (4, 15),
    "title_max_chars": (12, 30),
}


@dataclass(frozen=True)
class ActivePolicy:
    profile: str
    thresholds: PolicyThresholds
    overrides: dict[str, Any]
    has_custom_overrides: bool
    updated_by: str
    updated_at: datetime | None


_cache_lock = threading.Lock()
_cached_policy: ActivePolicy | None = None
_cached_at: float = 0.0


def invalidate_policy_cache() -> None:
    global _cached_policy, _cached_at
    with _cache_lock:
        _cached_policy = None
        _cached_at = 0.0


def default_policy_from_settings() -> ActivePolicy:
    """无数据库会话时的离线回退（直接对齐 Settings，保证纯函数单测兼容）。"""
    return ActivePolicy(
        profile="balanced",
        thresholds=PolicyThresholds(
            event_confidence_min=float(settings.EVENT_CONFIDENCE_MIN),
            not_event_confidence_min=float(settings.NOT_EVENT_CONFIDENCE_MIN),
            freshness_hours=int(settings.FRESHNESS_HOURS),
            dedup_trgm_threshold=float(settings.DEDUP_TRGM_THRESHOLD),
            title_min_chars=int(settings.TITLE_MIN_CHARS),
            title_max_chars=int(settings.TITLE_MAX_CHARS),
            require_full_text_for_candidate=False,
        ),
        overrides={},
        has_custom_overrides=False,
        updated_by="system",
        updated_at=None,
    )


def _merge_thresholds(profile: str, overrides: dict[str, Any] | None) -> tuple[PolicyThresholds, dict[str, Any]]:
    preset = PRESETS.get(profile, PRESETS["balanced"]).thresholds
    preset_dict = asdict(preset)
    base = dict(preset_dict)
    for key, raw_val in (overrides or {}).items():
        if key not in base or raw_val is None:
            continue
        if key == "require_full_text_for_candidate":
            val: Any = bool(raw_val)
        elif key in ("freshness_hours", "title_min_chars", "title_max_chars"):
            lo, hi = THRESHOLD_BOUNDS[key]
            val = max(int(lo), min(int(hi), int(raw_val)))
        else:
            lo, hi = THRESHOLD_BOUNDS[key]
            val = round(max(float(lo), min(float(hi), float(raw_val))), 2)
        base[key] = val

    # 先执行交叉边界修正，再生成 clean_overrides，确保落库值与实际生效值完全一致
    if base["title_min_chars"] > base["title_max_chars"]:
        base["title_min_chars"] = base["title_max_chars"]

    clean_overrides = {k: v for k, v in base.items() if v != preset_dict[k]}
    return PolicyThresholds(**base), clean_overrides


def get_active_policy(session: Session | None = None, *, bypass_cache: bool = False) -> ActivePolicy:
    """读取当前生效的风控策略与阈值。"""
    global _cached_policy, _cached_at
    if session is None:
        return default_policy_from_settings()

    now_mono = time.monotonic()
    if not bypass_cache:
        with _cache_lock:
            if _cached_policy is not None and (now_mono - _cached_at) < _CACHE_TTL_SEC:
                return _cached_policy

    row = session.get(RiskPolicyConfig, POLICY_ROW_ID)
    if row is None:
        policy = default_policy_from_settings()
    else:
        profile = row.profile if row.profile in POLICY_PROFILES else "balanced"
        thresholds, clean_overrides = _merge_thresholds(profile, row.overrides)
        policy = ActivePolicy(
            profile=profile,
            thresholds=thresholds,
            overrides=clean_overrides,
            has_custom_overrides=bool(clean_overrides),
            updated_by=row.updated_by or "system",
            updated_at=row.updated_at,
        )

    with _cache_lock:
        _cached_policy = policy
        _cached_at = now_mono
    return policy


def save_policy(
    session: Session,
    *,
    profile: str,
    overrides: dict[str, Any] | None = None,
    reset_overrides: bool = False,
    updated_by: str = "admin",
) -> ActivePolicy:
    """持久化风控档位与阈值覆盖并立即刷新缓存。"""
    if profile not in POLICY_PROFILES:
        raise ValueError(f"不支持的风控档位：{profile}")

    row = session.get(RiskPolicyConfig, POLICY_ROW_ID)
    if row is None:
        row = RiskPolicyConfig(id=POLICY_ROW_ID, profile=profile, overrides={}, updated_by=updated_by)
        session.add(row)

    next_overrides = {} if reset_overrides else (dict(overrides) if overrides is not None else dict(row.overrides or {}))
    thresholds, clean_overrides = _merge_thresholds(profile, next_overrides)
    row.profile = profile
    row.overrides = clean_overrides
    row.updated_by = updated_by
    session.flush()

    invalidate_policy_cache()
    return ActivePolicy(
        profile=profile,
        thresholds=thresholds,
        overrides=clean_overrides,
        has_custom_overrides=bool(clean_overrides),
        updated_by=updated_by,
        updated_at=row.updated_at,
    )
