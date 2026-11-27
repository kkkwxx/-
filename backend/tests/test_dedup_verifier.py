"""06 去重与时效门禁 + 07 轻校验器 + JPL CNEOS 适配器 + 预算熔断单测（不依赖真实外网/LLM）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import respx

from app.services import ai
from app.services.adapters import ApiAdapter
from app.services.dedup_freshness import check_freshness, make_title_fingerprint
from app.services.generator import stage_generate
from app.services.preprocess import FilterResult, judge_from_data
from app.services.verifier import verify_content, verify_title


def test_title_fingerprint_normalizes_punctuation():
    assert make_title_fingerprint("小行星贴脸地球！") == make_title_fingerprint("小行星 贴脸 地球")
    assert make_title_fingerprint("科学家抓到“鬼”了？") == make_title_fingerprint("科学家抓到鬼了")


def test_freshness_allows_future_asteroid_flyby():
    now = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    res = check_freshness(
        publish_time=now - timedelta(hours=3),
        event_time=now + timedelta(days=3),
        first_seen_at=now - timedelta(hours=2),
        is_future_event=True,
        now=now,
    )
    assert res.passed is True
    assert res.expired is False
    assert res.risk_flags == []


def test_freshness_expires_old_publish_time():
    now = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    res = check_freshness(
        publish_time=now - timedelta(hours=72),
        event_time=now - timedelta(hours=72),
        first_seen_at=now - timedelta(hours=1),
        now=now,
    )
    assert res.passed is False
    assert res.expired is True
    assert (res.reject_reason or "").startswith("expired:")


def test_freshness_flags_stale_rehashed_event():
    now = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    res = check_freshness(
        publish_time=now - timedelta(hours=4),
        event_time=now - timedelta(days=30),
        first_seen_at=now - timedelta(hours=1),
        now=now,
    )
    assert res.passed is True
    assert "stale_event" in res.risk_flags


def test_verifier_blocks_prohibited_claims_and_spoiler():
    bad1 = verify_title("科学家抓到外星人已确认存在！")
    assert bad1.passed is False
    assert "prohibited_claim" in bad1.flags

    bad2 = verify_title("科学家抓到鬼了？原来是中微子")
    assert bad2.passed is False
    assert "title_has_spoiler" in bad2.flags

    good = verify_title("科学家抓到“鬼”了？")
    assert good.passed is True
    assert good.effective_chars == 7


def test_verifier_requires_disclaimer_for_supernatural():
    bad = verify_content(
        title_final="中国道士杀进泰国灵异综艺！",
        body_lead="中国道士参加泰国通灵挑战，现场展示了令人惊叹的神秘力量。",
        body_summary="节目播出后引发了极大关注，网友纷纷猜测其中是否隐藏着真正的超自然能力与东方秘术，画面非常震撼。",
        fact_anchor="泰国灵异综艺节目播出",
        event_type="anomaly_psychic",
        content_nature="娱乐化灵异",
        source_level="B",
    )
    assert bad.passed is False
    assert "missing_supernatural_disclaimer" in bad.risk_flags


def test_off_topic_other_is_rejected_without_signals():
    fr = FilterResult(
        cleaned_title="某地发生 4.5 级地震",
        hard=None,
        soft=None,
        anchors=set(),
        verbs=["发生"],
        signals=[],
        whitelisted=False,
        priority=15,
    )
    judged = judge_from_data(
        {
            "is_event": True,
            "event_type": "other",
            "category": "earthquake",
            "content_nature": "科学事实",
            "confidence": 0.9,
            "reason": "普通地震新闻，非泛宇宙选题",
        },
        filter_result=fr,
        source_level="B",
    )
    assert judged.status == "rule_rejected"
    assert judged.reject_reason == "off_topic:other"


def test_generator_retries_once_on_verify_failure(monkeypatch):
    bad_payload = {
        "fact_anchor": "社交平台流传 UFO 视频",
        "title_candidates": [
            {"title": "外星人已确认降临地球！", "style_tag": "恐怖片式", "hook_score": 5}
        ],
        "body_lead": "网上流传一段夜空视频，外星人已确认光临地球。",
        "body_summary": "这是一段引发广泛讨论的短视频，画面中出现发光物体，不少网友对它的真实来源展开了激烈争论。",
        "self_risk_flags": [],
    }
    good_payload = {
        "fact_anchor": "社交平台流传未证实夜空发光物视频",
        "title_candidates": [
            {"title": "夜空惊现三角光点，细思极恐？", "style_tag": "恐怖片式", "hook_score": 4},
            {"title": "神秘发光物盘旋夜空，UFO现形了？", "style_tag": "科幻片式", "hook_score": 4},
            {"title": "网友拍到天上飘着诡异亮光？", "style_tag": "活久见式", "hook_score": 3},
        ],
        "body_lead": "这是社交平台流传的夜空发光物视频，目前未经证实，属于网络讨论，别当真。",
        "body_summary": "一段拍摄于夜空的发光体视频在社交平台走红，由于缺乏权威来源与定位核验，有人猜测是不明飞行物（UFO），也有人认为是无人机编队，目前尚无可靠证据。",
        "self_risk_flags": [],
    }
    calls = {"n": 0}

    def fake_gen(*_args, **_kwargs):
        calls["n"] += 1
        data = bad_payload if calls["n"] == 1 else good_payload
        return ai.AiResult(data=data, tokens=100, latency_ms=30, status="success")

    monkeypatch.setattr(ai, "generate", fake_gen)
    res = stage_generate(
        title="网友拍到夜空三角形发光物",
        summary="无来源视频",
        full_text=None,
        has_full_text=False,
        publisher_name="微博",
        source_level="C",
        event_type="anomaly_psychic",
        content_nature="未证实目击",
        event_confidence=0.8,
        event_time=None,
    )
    assert calls["n"] == 2
    assert res.status == "pending"
    assert res.title_final == "夜空惊现三角光点，细思极恐？"


@respx.mock
def test_jpl_cneos_api_adapter_parses_close_approach():
    url = "https://ssd-api.jpl.nasa.gov/cad.api?dist-max=0.05&date-min=now&date-max=%2B7"
    sample = {
        "signature": {"source": "NASA/JPL SBDB Close Approach Data API", "version": "1.4"},
        "count": "1",
        "fields": ["des", "orbit_id", "jd", "cd", "dist", "dist_min", "dist_max", "v_rel", "v_inf", "t_sigma_f", "h"],
        "data": [["2026 XX1", "3", "2461375.1", "2026-Nov-29 14:22", "0.0021", "0.0020", "0.0022", "12.4", "12.3", "< 00:01", "25.1"]],
    }
    respx.get(url).mock(return_value=httpx.Response(200, text=json.dumps(sample)))
    res = ApiAdapter().fetch(url=url, source_id=99)
    assert res.error_kind is None
    assert len(res.items) == 1
    item = res.items[0]
    assert "2026 XX1" in item.title
    assert "比月球还近" in item.title
    assert item.publisher_url == "https://cneos.jpl.nasa.gov"
    assert item.extra["is_future_event"] is True
