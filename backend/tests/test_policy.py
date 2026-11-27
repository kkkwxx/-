"""风控严厉度档位开关与阈值动态配置测试（M6）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.dedup_freshness import check_freshness
from app.services.policy import invalidate_policy_cache
from app.services.verifier import verify_title


def test_policy_api_switch_profiles_and_custom_overrides(client):
    invalidate_policy_cache()

    # 1. 默认均衡档位
    r0 = client.get("/api/policy")
    assert r0.status_code == 200
    d0 = r0.json()
    assert d0["profile"] == "balanced"
    assert d0["thresholds"]["freshness_hours"] == 48
    assert len(d0["presets"]) == 3

    # 2. 一键切到严格风控档位
    r1 = client.put("/api/policy", json={"profile": "strict"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["profile"] == "strict"
    assert d1["thresholds"]["event_confidence_min"] == 0.85
    assert d1["thresholds"]["freshness_hours"] == 24
    assert d1["thresholds"]["title_min_chars"] == 10
    assert d1["thresholds"]["require_full_text_for_candidate"] is True
    assert d1["has_custom_overrides"] is False

    # 验证动态阈值直接作用于轻校验与时效门禁
    t_strict = verify_title(
        "科学家抓到“鬼”了？",
        title_min_chars=d1["thresholds"]["title_min_chars"],
        title_max_chars=d1["thresholds"]["title_max_chars"],
    )
    assert t_strict.passed is False  # 严格档要求 10-18 字，7 字不通过

    now = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    f_strict = check_freshness(
        publish_time=now - timedelta(hours=30),
        event_time=None,
        first_seen_at=now,
        now=now,
        freshness_hours=d1["thresholds"]["freshness_hours"],
    )
    assert f_strict.expired is True

    # 3. 切到宽松召回并附带自定义阈值覆盖
    r2 = client.put(
        "/api/policy",
        json={"profile": "relaxed", "freshness_hours": 96, "event_confidence_min": 0.60},
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["profile"] == "relaxed"
    assert d2["thresholds"]["freshness_hours"] == 96
    assert d2["thresholds"]["event_confidence_min"] == 0.60
    assert d2["has_custom_overrides"] is True

    # 4. 同档位增量更新不覆盖既有自定义阈值
    r_inc = client.put(
        "/api/policy",
        json={"profile": "relaxed", "title_min_chars": 8},
    )
    assert r_inc.status_code == 200
    d_inc = r_inc.json()
    assert d_inc["thresholds"]["freshness_hours"] == 96  # 上一步自定义值被保留
    assert d_inc["thresholds"]["title_min_chars"] == 8

    # 5. 非法区间（title_min_chars > title_max_chars）明确返回 400
    r_bad = client.put(
        "/api/policy",
        json={"profile": "balanced", "title_min_chars": 15, "title_max_chars": 12},
    )
    assert r_bad.status_code == 400

    # 6. 一键恢复档位默认
    r3 = client.put("/api/policy", json={"profile": "balanced", "reset_overrides": True})
    assert r3.status_code == 200
    assert r3.json()["has_custom_overrides"] is False
    assert r3.json()["thresholds"]["freshness_hours"] == 48
