"""信源与域名规则种子（口径：03-信源管理 §10）。

用法：

    uv run python -m app.seed                      # 灌种子（幂等）
    uv run python -m app.seed --verify             # 灌种子 + 逐源探活对账（03 §10.3）
    uv run python -m app.seed --hash-password xxx  # 生成 ADMIN_PASSWORD_HASH

两条铁律：

1. **已有记录只补缺失字段，绝不覆盖人改过的配置**（03 §10.2）。
   enabled / poll_interval_min 不覆盖；expected_status_on_first_run 例外，
   它只是验收期望值，每次 seed 都按 fixtures 更新。
2. `--verify` 探活是**只读检查**：只回写 source 的 last_validated_at / health_status，
   不写 task_run、不写 raw_item。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt
import yaml
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT
from app.core.db import SessionLocal, engine
from app.models import (
    Article,
    EventCluster,
    KeywordRule,
    PublishedArticle,
    PushRecord,
    RawItem,
    Source,
    SourceDomainRule,
)

# fixtures 在仓库根，不在 backend/ 里（REPO_ROOT 已指向仓库根）
FIXTURES_DIR: Path = REPO_ROOT / "fixtures"
SOURCE_SEED_FILE = "source_seed.yaml"
DOMAIN_RULE_SEED_FILE = "domain_rule_seed.yaml"
KEYWORD_RULE_SEED_FILE = "keyword_rule_seed.yaml"
# 建表检查用：models 里的全部业务表

REQUIRED_TABLES = (
    "source",
    "source_domain_rule",
    "unknown_domain",
    "task_run",
    "raw_item",
    "article",
    "review_action",
    "keyword_rule",
    "ai_run",
    "event_cluster",
    "published_article",
    "push_record",
    "risk_policy_config",
)

# 已存在时允许「补缺失」的字段（enabled / poll_interval_min 明确不在其中）
SOURCE_FILLABLE_FIELDS = (
    "name",
    "type",
    "level",
    "url",
    "query_template",
    "expected_domains",
    "title_suffix_rules",
    "extra_headers",
    "api_key_env",
    "note",
)

# manual 类型没有 url、也不会被调度，探活毫无意义（对它会拿到 MISSING_URL → down 的假故障）
NOT_PROBED_TYPES = frozenset({"manual"})

# --verify 的探活范围：enabled=true 的全部源 + 这些演示 / 故障样本
DEMO_SOURCE_CODES = (
    "phys_neuro_legacy",
    "esa_backup_html",
    "atel",
    "esa_news",
    "tianapi_science",
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _load_fixture(file_name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / file_name
    if not path.is_file():
        raise SystemExit(f"找不到 fixtures 文件：{path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"fixtures 文件格式错误（应为列表）：{path}")
    return data


def _is_empty(value: Any) -> bool:
    """None / 空串 / 空列表 / 空字典都算「缺失」，可以从 fixtures 补。"""
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) == 0
    return False


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def seed_domain_rules(session: Session) -> None:
    """按 domain upsert。已存在的规则不覆盖 level / note，只补缺失。"""
    items = _load_fixture(DOMAIN_RULE_SEED_FILE)
    existing = {rule.domain: rule for rule in session.scalars(select(SourceDomainRule))}

    for item in items:
        domain = item["domain"]
        rule = existing.get(domain)
        if rule is None:
            created = SourceDomainRule(
                domain=domain,
                level=item["level"],
                note=item.get("note"),
                enabled=True,
            )
            session.add(created)
            existing[domain] = created
            continue
        # 已存在：运营改过的等级与备注一律保留，只在字段为空时补
        if _is_empty(rule.note) and not _is_empty(item.get("note")):
            rule.note = item["note"]

    session.commit()


def seed_sources(session: Session) -> None:
    """按 code upsert。已存在的源只补缺失字段，不覆盖人改过的配置。"""
    items = _load_fixture(SOURCE_SEED_FILE)
    existing = {src.code: src for src in session.scalars(select(Source))}

    for item in items:
        code = item["code"]
        src = existing.get(code)
        enabled = bool(item.get("enabled", True))

        if src is None:
            created = Source(
                code=code,
                name=item["name"],
                type=item["type"],
                level=item["level"],
                url=item.get("url"),
                query_template=item.get("query_template"),
                poll_interval_min=int(item.get("poll_interval_min") or 30),
                enabled=enabled,
                health_status="ok" if enabled else "disabled",
                expected_domains=list(item.get("expected_domains") or []),
                title_suffix_rules=list(item.get("title_suffix_rules") or []),
                extra_headers=dict(item.get("extra_headers") or {}),
                api_key_env=item.get("api_key_env"),
                note=item.get("note"),
                expected_status_on_first_run=item.get("expected_status_on_first_run"),
            )
            session.add(created)
            existing[code] = created
            continue

        # 已存在：值为 None / 空串 / 空列表 / 空字典的字段才从 fixtures 补
        for field in SOURCE_FILLABLE_FIELDS:
            seed_value = item.get(field)
            if _is_empty(seed_value):
                continue
            if _is_empty(getattr(src, field)):
                setattr(src, field, seed_value)

        # 验收期望值不属于「人的配置」，每次都同步
        expected = item.get("expected_status_on_first_run")
        if expected is not None and src.expected_status_on_first_run != expected:
            src.expected_status_on_first_run = expected

        # enabled=false 的源，健康状态必须与之一致（03 §7.1 disabled 的进入条件）
        want_status = "ok" if src.enabled else "disabled"
        if not src.enabled and src.health_status != "disabled":
            src.health_status = "disabled"
        elif src.enabled and src.health_status == "disabled":
            src.health_status = want_status

    session.commit()



def seed_keyword_rules(session: Session) -> int:
    """按 ``(type, category, pattern)`` upsert 词表（05 §3 / §4）。

    已存在的规则只补空的 ``weight`` / ``note``，不覆盖运营改过的值
    ——与 ``seed_sources`` 同一条纪律（03 §10.2）。返回表内总条数。
    """
    items = _load_fixture(KEYWORD_RULE_SEED_FILE)
    existing = {
        (rule.type, rule.category, rule.pattern): rule
        for rule in session.scalars(select(KeywordRule))
    }

    for item in items:
        key = (item["type"], item["category"], item["pattern"])
        rule = existing.get(key)
        if rule is None:
            created = KeywordRule(
                type=item["type"],
                category=item["category"],
                pattern=item["pattern"],
                weight=int(item.get("weight") or 0),
                scope=item.get("scope") or "title_summary",
                enabled=True,
                note=item.get("note"),
            )
            session.add(created)
            existing[key] = created
            continue
        # 已存在：只补空值
        if not rule.weight and item.get("weight"):
            rule.weight = int(item["weight"])
        if _is_empty(rule.note) and not _is_empty(item.get("note")):
            rule.note = item["note"]

    session.commit()
    return int(session.scalar(select(func.count()).select_from(KeywordRule)) or 0)


# ---------------------------------------------------------------------------
# 建表检查
# ---------------------------------------------------------------------------


def ensure_tables() -> None:
    """没跑迁移就直接 seed 会得到难懂的报错，这里先给一句人话。"""
    tables = set(inspect(engine).get_table_names())
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    if missing:
        raise SystemExit(
            "数据库还没建表（缺 "
            + ", ".join(missing)
            + "）。请先在 backend 目录执行：uv run alembic upgrade head"
        )


# ---------------------------------------------------------------------------
# 探活对账
# ---------------------------------------------------------------------------


def _probe_payload(src: Source) -> dict[str, Any]:
    return {
        "source_type": src.type,
        "url": src.url,
        "query_template": src.query_template,
        "extra_headers": src.extra_headers or {},
        "api_key_env": src.api_key_env,
        "source_id": src.id,
        "code": src.code,
    }


def _print_header() -> None:
    print(f"{'source':<22}{'expected':<14}{'actual':<14}match")


def verify() -> int:
    """逐源探活并与 expected_status_on_first_run 对账，返回进程退出码。"""
    try:
        from app.services.validate import probe_many, status_from_probe
    except ImportError as exc:  # 探活服务由采集侧提供，未就绪时给出人话
        print(
            "ERROR: 探活服务不可用（app.services.validate 导入失败）："
            f"{exc}\n"
            "       --verify 需要先完成 03-信源管理 §8 的探活实现（probe_many / status_from_probe）。",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as session:
        sources = list(session.scalars(select(Source).order_by(Source.code)))
        targets = [
            src for src in sources if src.enabled or src.code in DEMO_SOURCE_CODES
        ]
        probe_targets = [src for src in targets if src.type not in NOT_PROBED_TYPES]
        payloads = [_probe_payload(src) for src in probe_targets]
        results = probe_many(payloads) if payloads else []
        by_code = {str(item.get("code")): item for item in results}

        _print_header()
        matched = 0
        compared = 0
        skipped = 0
        for src in targets:
            expected = src.expected_status_on_first_run or "-"

            # manual 没有 url、也不会被调度，探活没有意义：列出来但记 SKIP，不参与计数
            if src.type in NOT_PROBED_TYPES:
                skipped += 1
                print(f"{src.code:<22}{expected:<14}{'skip':<14}SKIP")
                continue

            result = by_code.get(src.code)
            actual = status_from_probe(result) if result is not None else "unknown"
            ok = actual == expected
            compared += 1
            matched += 1 if ok else 0
            print(f"{src.code:<22}{expected:<14}{actual:<14}{'OK' if ok else 'MISMATCH'}")

            # 只回写探活时间与健康度：探活不是采集，不产生 task_run / raw_item
            if src.enabled and result is not None:
                src.last_validated_at = datetime.now(timezone.utc)
                src.health_status = actual

        session.commit()

    if skipped:
        print(f"# {skipped} 条 manual 源无 url、不会被调度，未探活（记 SKIP）")
    print(f"match {matched}/{compared}")
    return 0 if matched == compared else 1


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------


def hash_password(raw: str) -> str:
    """生成 ADMIN_PASSWORD_HASH 用的 bcrypt 哈希。"""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def seed_demo_articles(session: Session) -> int:
    """将 fixtures/eval/acceptance_10.yaml 的 10 条验收样例幂等灌库，用于离线演示与审核台体验。"""
    from datetime import timedelta

    from app.services.collector import sha256
    from app.services.dedup_freshness import make_title_fingerprint
    from app.services.verifier import default_disclaimer_for

    demo_path = FIXTURES_DIR / "eval" / "acceptance_10.yaml"
    if not demo_path.is_file():
        return 0
    cases = yaml.safe_load(demo_path.read_text(encoding="utf-8")) or []
    manual_src = session.execute(select(Source).where(Source.code == "manual")).scalar_one_or_none()
    if manual_src is None:
        return 0

    now = datetime.now(timezone.utc)
    inserted = 0
    for idx, case in enumerate(cases):
        url = case["url"]
        url_hash = sha256(url)
        exists = session.execute(
            select(RawItem).where(RawItem.source_id == manual_src.id, RawItem.url_hash == url_hash)
        ).scalar_one_or_none()
        if exists is not None:
            continue

        pub_time = now - timedelta(hours=idx + 1)
        raw = RawItem(
            source_id=manual_src.id,
            url=url,
            resolved_url=url,
            publisher_url=url,
            publisher_name=case.get("publisher_name"),
            raw_title=case["title"],
            raw_summary=case.get("summary"),
            full_text=case.get("summary"),
            full_text_status="ok",
            published_at=pub_time,
            fetched_at=now,
            extra={"demo_case_id": case["id"]},
            url_hash=url_hash,
        )
        session.add(raw)
        session.flush()

        judge = case.get("judge_replay") or {}
        gen = case.get("generate_replay") or {}
        cands = gen.get("title_candidates") or []
        best = cands[0] if cands else {}
        status = case["expect_status"]
        ev_type = case.get("expect_event_type") or judge.get("event_type")
        nature = judge.get("content_nature")
        risk_flags: list[str] = []
        if ev_type == "anomaly_psychic" or nature in ("未证实目击", "灵异传闻", "娱乐化灵异", "伪科学主张"):
            risk_flags.append("supernatural")
        if case["source_level"] == "C":
            risk_flags.append("c_level_source")

        cluster_id = None
        if status in ("candidate", "pending") and ev_type:
            cluster = EventCluster(
                event_type=ev_type,
                primary_entity=(judge.get("entities") or [case["name"]])[0][:128],
                event_action=judge.get("event_action") or "release",
                event_date=pub_time.date(),
                canonical_title=case["title"],
                first_seen_at=now,
                article_count=1,
                is_published=False,
            )
            session.add(cluster)
            session.flush()
            cluster_id = cluster.id

        article = Article(
            raw_item_id=raw.id,
            title=case["title"],
            summary=case.get("summary"),
            publisher_name=case.get("publisher_name"),
            publisher_url=url,
            original_url=url,
            canonical_url=url,
            source_level=case["source_level"],
            source_level_reason=f"demo:{case['source_level']}",
            resolve_status="resolved",
            url_hash=url_hash,
            content_hash=sha256(case["title"] + "|" + (case.get("summary") or "")),
            title_fingerprint=make_title_fingerprint(case["title"]),
            first_seen_at=now,
            is_future_event=bool(case.get("is_future_event", False)),
            event_cluster_id=cluster_id,
            entities=list(judge.get("entities") or []),
            event_action=judge.get("event_action"),
            is_event=judge.get("is_event") if judge else False,
            event_type=ev_type,
            content_nature=nature,
            event_confidence=judge.get("confidence"),
            risk_flags=risk_flags,
            review_priority={"A": 45, "B": 30, "C": 10}.get(case["source_level"], 10),
            fact_anchor=gen.get("fact_anchor"),
            title_candidates=[
                {**c, "passed_check": True, "check_errors": []} for c in cands
            ],
            title_final=best.get("title"),
            body_lead=gen.get("body_lead"),
            body_summary=gen.get("body_summary"),
            hook_score=best.get("hook_score"),
            style_tag=best.get("style_tag"),
            need_review=True,
            ai_generated_at=now if gen else None,
            status=status,
            reject_reason=case.get("expect_reject_prefix"),
        )
        session.add(article)
        session.flush()

        if cluster_id:
            cluster_obj = session.get(EventCluster, cluster_id)
            if cluster_obj:
                cluster_obj.lead_article_id = article.id

        # 默认把首条“江门中微子”样例额外发布一条快照到栏目页，打开 /column 即有真实效果
        if case["id"] == "case01_neutrino_result":
            article.status = "published"
            pub = PublishedArticle(
                article_id=article.id,
                event_cluster_id=cluster_id,
                version=1,
                title_final=article.title_final or article.title,
                body_lead=article.body_lead or "",
                body_summary=article.body_summary or "",
                event_type=ev_type or "deep_space_particle",
                source_name=article.publisher_name or "官方来源",
                source_url=url,
                source_level=article.source_level,
                content_nature=nature,
                disclaimer=default_disclaimer_for(ev_type, nature),
                style_tag=article.style_tag,
                hook_score=article.hook_score,
                is_active=True,
                published_at=now,
                operator="seed-demo",
            )
            session.add(pub)
            session.flush()
            session.add(
                PushRecord(
                    published_article_id=pub.id,
                    action="publish",
                    channel="internal_column",
                    payload_json={"demo": True, "title_final": pub.title_final},
                    status="success",
                    operator="seed-demo",
                )
            )
            if cluster_id:
                cluster_obj = session.get(EventCluster, cluster_id)
                if cluster_obj:
                    cluster_obj.is_published = True
                    cluster_obj.published_at = now

        inserted += 1

    session.commit()
    return inserted


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed",
        description="灌入信源与域名规则种子（03-信源管理 §10）",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="灌种子后逐源探活，与 expected_status_on_first_run 对账",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="同时灌入 10 条需求验收演示样例（含 1 条已发布栏目快照）",
    )
    parser.add_argument(
        "--hash-password",
        metavar="PLAINTEXT",
        help="打印明文密码的 bcrypt 哈希（供 .env 的 ADMIN_PASSWORD_HASH 使用）",
    )
    args = parser.parse_args(argv)

    if args.hash_password:
        print(hash_password(args.hash_password))
        return 0

    ensure_tables()
    with SessionLocal() as session:
        seed_domain_rules(session)
        seed_sources(session)
        keyword_count = seed_keyword_rules(session)
        source_count = session.scalar(select(func.count()).select_from(Source)) or 0
        rule_count = (
            session.scalar(select(func.count()).select_from(SourceDomainRule)) or 0
        )
        demo_inserted = seed_demo_articles(session) if args.demo else 0

    print(f"seeded {source_count} sources, {rule_count} domain rules")
    print(f"keyword rules: {keyword_count}")
    if args.demo:
        print(f"demo acceptance articles inserted: {demo_inserted}")

    if not args.verify:
        return 0
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
