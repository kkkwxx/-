"""06 去重与时效门禁：两级去重（L1 标题指纹/pg_trgm + L2 跨源事件簇）+ 48h/未来事件时效门禁。

口径：需求文档 v1.0 §5.3 / §6 / §8、01-总体架构 §2 / §6。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.pipeline import Article, RawItem
from app.models.publish import EventCluster

logger = logging.getLogger(__name__)

# 去除所有中英文标点、符号、空白，只保留字母数字和汉字，用于标题规范化指纹
_FP_STRIP_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)

# 用于判断是否命中“更高信源等级”
_LEVEL_RANK = {"A": 3, "B": 2, "C": 1}

# 忽略已拒绝/重复/过期的文章，不作为去重基准
_EXCLUDED_STATUSES = ("rule_rejected", "duplicate", "expired", "rejected")


def make_title_fingerprint(title: str | None) -> str:
    """规范化标题指纹：NFKC → 小写 → 去标点与空白。"""
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", title).lower()
    return _FP_STRIP_RE.sub("", text)


def _norm_entity(value: str | None) -> str:
    if not value:
        return ""
    return _FP_STRIP_RE.sub("", unicodedata.normalize("NFKC", value).lower())


@dataclass
class DedupOutcome:
    is_duplicate: bool = False
    cluster_id: int | None = None
    matched_article_id: int | None = None
    reason: str | None = None
    cross_verified_upgrade: bool = False


@dataclass
class FreshnessOutcome:
    passed: bool = True
    expired: bool = False
    reject_reason: str | None = None
    risk_flags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# L1 即时去重（AI 判定之前，零 LLM 成本）
# --------------------------------------------------------------------------- #


def check_l1_duplicate(
    session: Session,
    article: Article,
    *,
    now: datetime | None = None,
    trgm_threshold: float | None = None,
) -> DedupOutcome:
    """按标题规范化指纹 + content_hash + pg_trgm（或 rapidfuzz 兜底）做即时去重。"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=int(settings.DEDUP_WINDOW_HOURS))
    fp = make_title_fingerprint(article.title)
    article.title_fingerprint = fp

    if not fp:
        return DedupOutcome(is_duplicate=False)

    # 1. 精确指纹或 content_hash 命中
    exact = session.execute(
        select(Article)
        .where(
            Article.id != article.id,
            Article.created_at >= cutoff,
            Article.status.not_in(_EXCLUDED_STATUSES),
            (Article.title_fingerprint == fp)
            | (
                Article.content_hash.is_not(None)
                & (Article.content_hash == article.content_hash)
            ),
        )
        .order_by(Article.id.asc())
        .limit(1)
    ).scalar_one_or_none()

    if exact is not None:
        return DedupOutcome(
            is_duplicate=True,
            cluster_id=exact.event_cluster_id,
            matched_article_id=exact.id,
            reason=f"dup:l1_exact:article#{exact.id}",
        )

    # 2. PostgreSQL pg_trgm 相似度检索（若当前方言不支持 pg_trgm，如 SQLite 单测，回退 rapidfuzz）
    threshold = float(trgm_threshold if trgm_threshold is not None else settings.DEDUP_TRGM_THRESHOLD)
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        try:
            sim_expr = func.similarity(Article.title, article.title)
            near = session.execute(
                select(Article, sim_expr.label("sim"))
                .where(
                    Article.id != article.id,
                    Article.created_at >= cutoff,
                    Article.status.not_in(_EXCLUDED_STATUSES),
                    sim_expr >= threshold,
                )
                .order_by(sim_expr.desc(), Article.id.asc())
                .limit(1)
            ).first()
            if near is not None:
                matched_art, sim_val = near[0], float(near[1] or 0)
                return DedupOutcome(
                    is_duplicate=True,
                    cluster_id=matched_art.event_cluster_id,
                    matched_article_id=matched_art.id,
                    reason=f"dup:l1_trgm({sim_val:.2f}):article#{matched_art.id}",
                )
        except Exception:
            logger.debug("pg_trgm 不可用，回退 rapidfuzz 相似度去重")

    # 3. 兜底 rapidfuzz（扫描窗口内最近 200 条）
    recent = session.execute(
        select(Article)
        .where(
            Article.id != article.id,
            Article.created_at >= cutoff,
            Article.status.not_in(_EXCLUDED_STATUSES),
        )
        .order_by(Article.id.desc())
        .limit(200)
    ).scalars().all()
    for other in recent:
        other_fp = other.title_fingerprint or make_title_fingerprint(other.title)
        if not other_fp:
            continue
        score = fuzz.ratio(fp, other_fp) / 100.0
        if score >= max(threshold, 0.80):
            return DedupOutcome(
                is_duplicate=True,
                cluster_id=other.event_cluster_id,
                matched_article_id=other.id,
                reason=f"dup:l1_fuzzy({score:.2f}):article#{other.id}",
            )

    return DedupOutcome(is_duplicate=False)


# --------------------------------------------------------------------------- #
# L2 跨源事件级去重 + event_cluster 维护（AI 判定后执行）
# --------------------------------------------------------------------------- #


def _entity_match(a_entities: list[str], cluster_entity: str, cluster_title: str) -> bool:
    c_norm = _norm_entity(cluster_entity)
    t_norm = _norm_entity(cluster_title)
    for raw in a_entities:
        e_norm = _norm_entity(raw)
        if len(e_norm) < 2:
            continue
        if c_norm and (e_norm == c_norm or e_norm in c_norm or c_norm in e_norm):
            return True
        if c_norm and fuzz.token_set_ratio(e_norm, c_norm) >= 82:
            return True
        if t_norm and len(e_norm) >= 4 and e_norm in t_norm:
            return True
    return False


def _choose_event_date(article: Article, raw: RawItem | None, now: datetime) -> date:
    dt = article.event_time or (raw.published_at if raw else None) or article.first_seen_at or now
    return dt.date()


def assign_event_cluster(
    session: Session,
    article: Article,
    raw: RawItem | None,
    *,
    now: datetime | None = None,
) -> DedupOutcome:
    """将通过 is_event 的文章归并到已有 ``event_cluster`` 或创建新簇。

    特性：
    - 同事件簇已存在（同类 + 日期 ±2 天 + 实体匹配）：
      - 簇已发布 → 直接标记 ``duplicate``（防重复推送）；
      - 簇未发布：
        - 若原主稿是 C 级待定，新入簇文章为 A/B 级 → 追加交叉印证提示到主稿，新文章入簇；
        - 其余情况标记 ``duplicate`` 并累加簇计数。
    - 无匹配簇 → 新建 ``event_cluster``，并将 ``article.first_seen_at`` 与簇对齐。
    """
    now = now or datetime.now(timezone.utc)
    if not article.event_type or article.event_type == "other":
        return DedupOutcome(is_duplicate=False)

    ev_date = _choose_event_date(article, raw, now)
    date_min = ev_date - timedelta(days=2)
    date_max = ev_date + timedelta(days=2)
    entities = [str(e).strip() for e in (article.entities or []) if str(e).strip()]

    candidates = session.execute(
        select(EventCluster)
        .where(
            EventCluster.event_type == article.event_type,
            EventCluster.event_date >= date_min,
            EventCluster.event_date <= date_max,
        )
        .order_by(EventCluster.id.asc())
    ).scalars().all()

    matched_cluster: EventCluster | None = None
    for cluster in candidates:
        if entities and _entity_match(entities, cluster.primary_entity, cluster.canonical_title):
            # 如果两者都有动作且动作不同但标题高度相似，也算同簇；否则实体+同类+±2天即视为同事件
            matched_cluster = cluster
            break
        # 实体为空时用标题模糊度兜底
        fp_a = make_title_fingerprint(article.title)
        fp_c = make_title_fingerprint(cluster.canonical_title)
        if fp_a and fp_c and fuzz.token_set_ratio(fp_a, fp_c) >= 78:
            matched_cluster = cluster
            break

    if matched_cluster is not None:
        article.event_cluster_id = matched_cluster.id
        article.first_seen_at = matched_cluster.first_seen_at
        matched_cluster.article_count = int(matched_cluster.article_count or 1) + 1

        if matched_cluster.is_published:
            return DedupOutcome(
                is_duplicate=True,
                cluster_id=matched_cluster.id,
                matched_article_id=matched_cluster.lead_article_id,
                reason=f"dup:cluster_published:cluster#{matched_cluster.id}",
            )

        lead = (
            session.get(Article, matched_cluster.lead_article_id)
            if matched_cluster.lead_article_id
            else None
        )
        upgraded = False
        if lead is not None:
            # 记录跨源交叉佐证（需求 §5.3：C 级线索等待 B 级以上来源确认）
            notes = list(lead.evidence_notes or [])
            src_url = article.original_url or article.publisher_url or (raw.url if raw else "")
            notes.append(
                {
                    "type": "cross_source_match",
                    "article_id": article.id,
                    "source_level": article.source_level,
                    "publisher_name": article.publisher_name,
                    "url": src_url,
                    "title": article.title,
                    "matched_at": now.isoformat(),
                }
            )
            lead.evidence_notes = notes
            if _LEVEL_RANK.get(article.source_level, 0) > _LEVEL_RANK.get(lead.source_level, 0):
                upgraded = True
                # 若原主稿因 C 级停在 pending 且新源是 A/B 级，补充标记供编辑快速确认
                flags = list(lead.risk_flags or [])
                if "cross_verified_by_ab" not in flags:
                    flags.append("cross_verified_by_ab")
                    lead.risk_flags = flags

        return DedupOutcome(
            is_duplicate=True,
            cluster_id=matched_cluster.id,
            matched_article_id=matched_cluster.lead_article_id,
            reason=f"dup:l2_cluster:cluster#{matched_cluster.id}",
            cross_verified_upgrade=upgraded,
        )

    # 未命中已有簇 → 建新簇
    first_seen = article.first_seen_at or (raw.fetched_at if raw else now)
    article.first_seen_at = first_seen
    cluster = EventCluster(
        event_type=article.event_type,
        primary_entity=(entities[0] if entities else article.title[:60])[:128],
        event_action=(article.event_action or "")[:64],
        event_date=ev_date,
        canonical_title=article.title,
        first_seen_at=first_seen,
        lead_article_id=article.id,
        article_count=1,
        is_published=False,
    )
    session.add(cluster)
    session.flush()
    article.event_cluster_id = cluster.id
    return DedupOutcome(is_duplicate=False, cluster_id=cluster.id)


# --------------------------------------------------------------------------- #
# 时效门禁（需求 §6 / §8：24—48 小时内 + 未来事件豁免）
# --------------------------------------------------------------------------- #


def check_freshness(
    *,
    publish_time: datetime | None,
    event_time: datetime | None,
    first_seen_at: datetime | None,
    is_future_event: bool = False,
    now: datetime | None = None,
    freshness_hours: int | None = None,
) -> FreshnessOutcome:
    """判断新闻是否满足推送时效门禁（支持动态配置小时数 + 未来事件豁免）。"""
    now = now or datetime.now(timezone.utc)
    hours = int(freshness_hours if freshness_hours is not None else settings.FRESHNESS_HOURS)
    max_age = timedelta(hours=hours)
    flags: list[str] = []

    ref_time = publish_time
    if ref_time is None:
        flags.append("missing_publish_time")
        ref_time = first_seen_at or now
    elif ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=timezone.utc)

    # 1. 发布时间超过时效上限（无论是否未来事件，报道本身过旧都算过期）
    if now - ref_time > max_age:
        return FreshnessOutcome(
            passed=False,
            expired=True,
            reject_reason=f"expired:publish_older_than_{hours}h",
            risk_flags=flags,
        )

    # 2. 校验事件发生时间
    if event_time is not None:
        ev = event_time if event_time.tzinfo is not None else event_time.replace(tzinfo=timezone.utc)
        if ev > now:
            # 未来事件（小行星飞掠、任务发射等）
            max_future = timedelta(days=int(settings.FUTURE_EVENT_MAX_DAYS))
            if is_future_event or (ev - now) <= max_future:
                return FreshnessOutcome(passed=True, expired=False, risk_flags=flags)
            # 过于遥远的未来预告（如明年/数月后），进待定提示人工把关
            flags.append("distant_future_event")
            return FreshnessOutcome(passed=True, expired=False, risk_flags=flags)
        # 过去事件但发生时间远早于 7 天（旧闻重炒/周年回顾）
        if now - ev > timedelta(days=7):
            flags.append("stale_event")

    return FreshnessOutcome(passed=True, expired=False, risk_flags=flags)
