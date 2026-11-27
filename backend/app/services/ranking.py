"""公开栏目「综合神秘热度榜」评分与角标计算（纯确定性公式，零 AI 消耗）。

评分维度：
1. 悬念吸引力 ``hook_score``（1–5，默认 3）× 18
2. 权威信源加权 ``source_level``（A=+25, B=+15, C=+5）
3. 跨源报道热度 ``cluster_article_count``（每多 1 个独立信源报道 +15，上限 +75）
4. 真实读者点击量 ``view_count``（前 50 次 × 6，超出部分 × 2，边际递减防刷榜）
5. 温和时间衰减系数 ``0.55 + 0.45 / (1 + age_hours / 72)``，新事件靠前且老经典不归零
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

SOURCE_LEVEL_WEIGHTS: dict[str, int] = {
    "A": 25,
    "B": 15,
    "C": 5,
}


class RankableSnapshot(Protocol):
    hook_score: int | None
    source_level: str
    view_count: int
    published_at: datetime


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_hot_score(
    snapshot: RankableSnapshot,
    *,
    cluster_article_count: int = 1,
    now: datetime | None = None,
) -> int:
    """计算综合神秘热度分（放大 10 倍取整，便于展示如 1280 热度）。"""
    ref_now = _ensure_aware(now or datetime.now(timezone.utc))
    pub_at = _ensure_aware(snapshot.published_at)

    hook = snapshot.hook_score if snapshot.hook_score is not None else 3
    hook = max(1, min(5, int(hook)))
    hook_pts = hook * 18

    source_pts = SOURCE_LEVEL_WEIGHTS.get((snapshot.source_level or "").upper(), 5)

    extra_sources = max(int(cluster_article_count or 1) - 1, 0)
    cluster_pts = min(extra_sources, 5) * 15

    vc = max(int(snapshot.view_count or 0), 0)
    view_pts = min(vc, 50) * 6 + max(vc - 50, 0) * 2

    age_hours = max((ref_now - pub_at).total_seconds() / 3600.0, 0.0)
    freshness = 0.55 + 0.45 / (1.0 + age_hours / 72.0)

    raw_score = (hook_pts + source_pts + cluster_pts + view_pts) * freshness * 10.0
    return max(int(round(raw_score)), 1)


def compute_rank_badge(
    snapshot: RankableSnapshot,
    *,
    cluster_article_count: int = 1,
    rank_index: int | None = None,
    now: datetime | None = None,
) -> str | None:
    """返回热榜展示角标（爆 / 新 / 热 / A源 / None）。"""
    ref_now = _ensure_aware(now or datetime.now(timezone.utc))
    pub_at = _ensure_aware(snapshot.published_at)
    age_hours = max((ref_now - pub_at).total_seconds() / 3600.0, 0.0)
    hook = int(snapshot.hook_score or 0)
    vc = int(snapshot.view_count or 0)
    cluster_n = int(cluster_article_count or 1)

    if cluster_n >= 2 or (rank_index is not None and rank_index <= 1 and hook >= 4):
        return "爆"
    if age_hours <= 24.0:
        return "新"
    if hook >= 4 or vc >= 5:
        return "热"
    if (snapshot.source_level or "").upper() == "A":
        return "A源"
    return None
