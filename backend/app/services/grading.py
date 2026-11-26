"""来源定级：03 §6 六步判定链。纯函数，无 IO，便于单测。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.domain import registrable_domain
from app.services.dto import RawItemDraft, SourceConfig

AGGREGATOR_TYPES = ("google_news", "api")


@dataclass(frozen=True, slots=True)
class GradeResult:
    level: str
    reason: str
    domain: str | None = None
    # rule6 命中时非空：调用方据此 upsert unknown_domain（03 §6.6）
    unknown_domain: str | None = None


LEVEL_TO_RULE = {"A": "rule2", "B": "rule3", "C": "rule4"}


def origin_domain(item: RawItemDraft, source: SourceConfig) -> str | None:
    """按源类型取出处域名（04 §9.2）。

    聚合源（google_news / api）**只能**取 publisher_url —— 它们的 url 域名恒为
    google.com / 聚合站域名，用 url 兜底会把全部条目判成同一级。
    """
    if source.type in AGGREGATOR_TYPES:
        raw = item.publisher_url
    else:
        raw = item.resolved_url or item.url
    return registrable_domain(raw)


def _expected_domain_set(source: SourceConfig) -> frozenset[str]:
    """expected_domains 也做归一，保证后缀精确比较（禁止 contains）。"""
    result = set()
    for raw in source.expected_domains:
        domain = registrable_domain(raw)
        if domain:
            result.add(domain)
    return frozenset(result)


def grade_article_source(
    item: RawItemDraft,
    source: SourceConfig,
    rules: Mapping[str, str],
) -> GradeResult:
    """返回等级与 reason（格式 `rule{N}:{domain}`）。

    `rules` 是 `source_domain_rule` 中 enabled 规则的域名→等级快照。
    """
    # ②③ 取出处域名并归一；取不到 → rule1
    domain = origin_domain(item, source)
    if not domain:
        return GradeResult(level="C", reason="rule1:no-origin")

    # ④ 查规则表
    hit = rules.get(domain)
    if hit in LEVEL_TO_RULE:
        return GradeResult(level=hit, reason=f"{LEVEL_TO_RULE[hit]}:{domain}", domain=domain)

    # ⑤ 兜底：rss + 源级 A + 域名在 expected_domains 内 → A
    if (
        source.type == "rss"
        and source.level == "A"
        and domain in _expected_domain_set(source)
    ):
        return GradeResult(level="A", reason=f"rule5:{domain}", domain=domain)

    # ⑥ 未收录 → C（fail-safe，进待定池），同时记 unknown_domain
    return GradeResult(
        level="C",
        reason=f"rule6:unknown:{domain}",
        domain=domain,
        unknown_domain=domain,
    )
