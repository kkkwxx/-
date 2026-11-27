"""需求文档 v1.0 §10 全部 10 条验收用例端到端测试（离线可复现，零真实 LLM 费用）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.core.config import REPO_ROOT
from app.services import ai
from app.services.dedup_freshness import check_freshness
from app.services.generator import stage_generate
from app.services.preprocess import RuleSet, judge_from_data, stage_filter
from app.services.verifier import verify_content

CASES = yaml.safe_load(
    (REPO_ROOT / "fixtures" / "eval" / "acceptance_10.yaml").read_text(encoding="utf-8")
)


def _load_seed_rules_offline() -> RuleSet:
    """直接从 fixtures/keyword_rule_seed.yaml 编译 RuleSet，离线零依赖可秒跑。"""
    import re

    items = yaml.safe_load(
        (REPO_ROOT / "fixtures" / "keyword_rule_seed.yaml").read_text(encoding="utf-8")
    )
    rules = RuleSet()
    verb_patterns: list[str] = []
    for row in items:
        compiled = re.compile(row["pattern"], re.I)
        rtype = row["type"]
        if rtype == "exclude_hard":
            rules.hard.append((row["category"], row["pattern"], compiled))
        elif rtype == "exclude_soft":
            rules.soft.append((row["category"], int(row.get("weight") or 0), compiled))
        elif rtype == "anchor":
            rules.anchors.append((row["category"], compiled))
        elif rtype == "signal":
            rules.signals.append((int(row.get("weight") or 0), compiled))
        elif rtype == "event_verb":
            verb_patterns.append(row["pattern"])
    if verb_patterns:
        rules.verbs = re.compile("|".join(f"(?:{p})" for p in verb_patterns), re.I)
    return rules


def _run_case_offline(case: dict, rules) -> dict:
    """用离线 replay 数据跑完整「过滤 → 判定 → 时效 → AI 加工与轻校验」链路。"""
    now = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    fr = stage_filter(
        case["title"],
        case.get("summary"),
        source_level=case["source_level"],
        rules=rules,
        publisher_name=case.get("publisher_name"),
    )
    if fr.hard is not None:
        cat, word = fr.hard
        return {
            "status": "rule_rejected",
            "reject_reason": f"rule:{cat}:{word}",
            "event_type": None,
            "generated": None,
        }

    judged = judge_from_data(
        case["judge_replay"],
        filter_result=fr,
        source_level=case["source_level"],
    )
    if judged.status == "rule_rejected":
        return {
            "status": "rule_rejected",
            "reject_reason": judged.reject_reason,
            "event_type": judged.event_type,
            "generated": None,
        }

    fresh = check_freshness(
        publish_time=now - timedelta(hours=2),
        event_time=judged.event_time,
        first_seen_at=now - timedelta(hours=1),
        is_future_event=bool(case.get("is_future_event") or judged.is_future_event),
        now=now,
    )
    assert fresh.passed is True

    # 用 monkeypatch 方式把 generate_replay 注入 ai.generate
    gen_replay = case["generate_replay"]
    orig_generate = ai.generate
    ai.generate = lambda *_a, **_kw: ai.AiResult(data=gen_replay, tokens=120, latency_ms=50, status="success")
    try:
        generated = stage_generate(
            title=fr.cleaned_title,
            summary=case.get("summary"),
            full_text=case.get("summary"),
            has_full_text=True,
            publisher_name=case.get("publisher_name"),
            source_level=case["source_level"],
            event_type=judged.event_type,
            content_nature=judged.content_nature,
            event_confidence=judged.confidence,
            event_time=judged.event_time,
            existing_risk_flags=judged.risk_flags,
        )
    finally:
        ai.generate = orig_generate

    return {
        "status": generated.status,
        "reject_reason": None,
        "event_type": judged.event_type,
        "generated": generated,
    }


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_acceptance_10_cases(case):
    rules = _load_seed_rules_offline()
    out = _run_case_offline(case, rules)

    assert out["status"] == case["expect_status"], f"{case['id']} 期望 {case['expect_status']}，实际 {out}"
    if "expect_reject_prefix" in case:
        assert (out["reject_reason"] or "").startswith(case["expect_reject_prefix"])
    if "expect_event_type" in case:
        assert out["event_type"] == case["expect_event_type"]

    if out["generated"] is not None:
        gen = out["generated"]
        assert gen.title_final
        assert gen.body_lead
        assert gen.body_summary
        assert gen.fact_anchor
        assert 3 <= len(gen.title_candidates) <= 5
        check = verify_content(
            title_final=gen.title_final,
            body_lead=gen.body_lead,
            body_summary=gen.body_summary,
            fact_anchor=gen.fact_anchor,
            event_type=out["event_type"],
            content_nature=case["judge_replay"]["content_nature"],
            source_level=case["source_level"],
        )
        assert check.passed is True, f"{case['id']} 轻校验未通过：{check.errors}"
        # 特殊验收点断言
        if case["id"] == "case01_neutrino_result":
            assert "鬼" in gen.title_final
            assert "中微子" in gen.body_lead
        elif case["id"] == "case03_asteroid_flyby":
            assert "贴脸" in gen.title_final
            assert "撞击威胁" in gen.body_lead
        elif case["id"] == "case05_exoplanet_discovery":
            assert "地球2.0" in gen.title_final
            assert "候选" in gen.body_lead
        elif case["id"] == "case06_bci_experiment":
            assert "驭剑" in gen.title_final
            assert "脑电" in gen.body_lead
        elif case["id"] == "case08_thai_taoist_show":
            assert out["status"] == "pending"
            assert "娱乐节目" in gen.body_lead
        elif case["id"] == "case10_ufo_unverified":
            assert out["status"] == "pending"
            assert "外星人已确认" not in gen.title_final
            assert "已确认外星人" not in gen.title_final
