"""交付审计 P0/P1 修复专项回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt

from app.api.deps import COOKIE_NAME, JWT_ALGORITHM
from app.core.config import settings
from app.models.pipeline import Article, RawItem
from app.models.publish import EventCluster
from app.services import ai
from app.services.fetch import check_safe_outbound_url
from app.services.verifier import DEFAULT_SUPERNATURAL_DISCLAIMER, verify_content, verify_title


def test_p0_verifier_blocks_obfuscated_prohibited_claims():
    # 插空格、零宽字符、小写变体均无法绕过红线词
    r1 = verify_title("科学家宣布外 星 人\u200b已确认降临！")
    assert r1.passed is False
    assert "prohibited_claim" in r1.flags

    r2 = verify_title("震惊！nasa 隐瞒真相曝光？")
    assert r2.passed is False
    assert "prohibited_claim" in r2.flags

    # 单字“综艺”不再误放行灵异类，必须有明确免责/娱乐限定语
    r3 = verify_content(
        title_final="中国道士杀进泰国灵异综艺！",
        body_lead="泰国一档热门综艺播出中国道士参赛画面，现场气氛非常紧张刺激。",
        body_summary="节目播出后引发了网友对东方神秘力量的好奇和猜测，片段在社交平台迅速走红并收获大量点赞转发。",
        fact_anchor="泰国灵异综艺节目播出",
        event_type="anomaly_psychic",
        content_nature="娱乐化灵异",
        source_level="B",
    )
    assert r3.passed is False
    assert "missing_supernatural_disclaimer" in r3.risk_flags


def test_p0_ai_json_parse_failure_still_accounts_tokens(monkeypatch):
    class FakeComp:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="not-valid-json"))],
                usage=SimpleNamespace(total_tokens=88),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeComp()))
    monkeypatch.setattr(ai, "OpenAI", lambda **_kw: fake_client)
    monkeypatch.setattr(ai.settings, "DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(ai.settings, "REPLAY_MODE", False)
    ai.reset_client()

    res = ai.classify({"title": "test"})
    assert res.status == "failed"
    assert res.tokens == 176  # 2 次尝试各耗 88 tokens，完整记账不丢失


def test_p0_v2_republish_not_self_locked_and_state_machine_guards(client, session, make_source):
    src = make_source(code="src_v2_guard", level="A")
    now = datetime.now(timezone.utc)
    raw = RawItem(
        source_id=src.id,
        url="https://www.nasa.gov/v2-test",
        resolved_url="https://www.nasa.gov/v2-test",
        publisher_url="https://www.nasa.gov",
        publisher_name="NASA",
        raw_title="江门中微子实验发布首个物理成果",
        raw_summary="实验团队发布首个高精度中微子振荡参数成果。",
        full_text="实验团队发布首个高精度中微子振荡参数成果。",
        full_text_status="ok",
        published_at=now - timedelta(hours=1),
        fetched_at=now,
        url_hash="hash_v2_guard",
    )
    session.add(raw)
    session.flush()

    cluster = EventCluster(
        event_type="deep_space_particle",
        primary_entity="江门中微子实验",
        event_action="release",
        event_date=now.date(),
        canonical_title=raw.raw_title,
        first_seen_at=now,
        article_count=1,
        is_published=False,
    )
    session.add(cluster)
    session.flush()

    art = Article(
        raw_item_id=raw.id,
        title=raw.raw_title,
        summary=raw.raw_summary,
        publisher_name="NASA",
        publisher_url="https://www.nasa.gov",
        original_url=raw.url,
        canonical_url=raw.url,
        source_level="A",
        source_level_reason="rule2:nasa.gov",
        resolve_status="resolved",
        url_hash=raw.url_hash,
        first_seen_at=now,
        event_cluster_id=cluster.id,
        is_event=True,
        event_type="deep_space_particle",
        content_nature="科学事实",
        event_confidence=0.96,
        risk_flags=["unverified_source", "stale_event"],
        fact_anchor="江门中微子实验发布首个物理成果。",
        title_final="科学家抓到“鬼”了？",
        body_lead="别怕，这里的“鬼”是几乎不跟物质反应的中微子，也叫“幽灵粒子”。中国江门中微子实验测到了关键数据。",
        body_summary="位于广东地下700米的江门中微子实验（捕捉幽灵粒子的探测器）团队发布首个物理成果，高精度测定中微子振荡关键参数，帮人类进一步探索宇宙物质起源。",
        hook_score=5,
        style_tag="恐怖片式",
        status="duplicate",
    )
    session.add(art)
    session.flush()
    cluster.lead_article_id = art.id
    session.flush()

    # 1. duplicate 状态不能直接 approve
    assert client.post(f"/api/articles/{art.id}/review", json={"action": "approve"}).status_code == 400

    # 2. 切为 candidate 后编辑保留上游风险标记 + 校验非法枚举
    art.status = "candidate"
    session.flush()
    assert client.patch(f"/api/articles/{art.id}", json={"event_type": "invalid_type"}).status_code == 400
    edited = client.patch(f"/api/articles/{art.id}", json={"culture_tag": "玄幽·水"})
    assert edited.status_code == 200
    assert "unverified_source" in edited.json()["risk_flags"]
    assert "stale_event" in edited.json()["risk_flags"]

    # 3. 审核通过 → 发布 v1 快照
    assert client.post(f"/api/articles/{art.id}/review", json={"action": "approve"}).status_code == 200
    v1 = client.post(f"/api/articles/{art.id}/publish", json={})
    assert v1.status_code == 201
    assert v1.json()["version"] == 1

    # 4. 修改已发布文章退回待审 → 重审通过 → 成功发布 v2（彻底不自锁！）
    re_edit = client.patch(f"/api/articles/{art.id}", json={"title_final": "地下700米逮到幽灵粒子！"})
    assert re_edit.status_code == 200
    assert re_edit.json()["status"] == "candidate"
    assert client.post(f"/api/articles/{art.id}/review", json={"action": "approve"}).status_code == 200
    v2 = client.post(f"/api/articles/{art.id}/publish", json={})
    assert v2.status_code == 201
    assert v2.json()["version"] == 2
    assert v2.json()["title_final"] == "地下700米逮到幽灵粒子！"


def test_p0_supernatural_disclaimer_cannot_be_stripped(client, session, make_source):
    src = make_source(code="src_sup_disc", level="B")
    now = datetime.now(timezone.utc)
    raw = RawItem(
        source_id=src.id,
        url="https://ent.ifeng.com/c/test-supernatural",
        resolved_url="https://ent.ifeng.com/c/test-supernatural",
        publisher_url="https://ent.ifeng.com",
        publisher_name="凤凰网",
        raw_title="泰国灵异综艺中国道士参赛",
        raw_summary="娱乐综艺节目话题",
        full_text="娱乐综艺节目话题",
        full_text_status="ok",
        published_at=now - timedelta(hours=1),
        fetched_at=now,
        url_hash="hash_sup_disc",
    )
    session.add(raw)
    session.flush()
    art = Article(
        raw_item_id=raw.id,
        title=raw.raw_title,
        publisher_name="凤凰网",
        original_url=raw.url,
        source_level="B",
        source_level_reason="rule3:ifeng.com",
        resolve_status="resolved",
        url_hash=raw.url_hash,
        first_seen_at=now,
        is_event=True,
        event_type="anomaly_psychic",
        content_nature="娱乐化灵异",
        fact_anchor="泰国灵异综艺中国道士参赛话题",
        title_final="中国道士杀进泰国灵异综艺！",
        body_lead="这是泰国灵异综艺《UNSEEN：通灵者之战》的参赛话题，属于娱乐节目，别当真。",
        body_summary="泰国通灵挑战真人秀因一位中国道士选手的登场在社交平台引发热烈讨论，节目展示传统民俗元素，属于强剧本化的娱乐节目效果，切勿当真。",
        status="approved",
    )
    session.add(art)
    session.flush()

    pub = client.post(f"/api/articles/{art.id}/publish", json={"disclaimer": "自定义补充"})
    assert pub.status_code == 201
    assert DEFAULT_SUPERNATURAL_DISCLAIMER in pub.json()["disclaimer"]


def test_p0_admin_jwt_rejects_wrong_username(session):
    from fastapi.testclient import TestClient
    from app.core.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides.pop(__import__("app.api.deps", fromlist=["require_admin"]).require_admin, None)
    try:
        wrong_token = jwt.encode(
            {"sub": "hacker", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
            settings.JWT_SECRET,
            algorithm=JWT_ALGORITHM,
        )
        with TestClient(app) as raw_client:
            # 未登录访问内部管理读接口应 401
            assert raw_client.get("/api/sources").status_code == 401
            assert raw_client.get("/api/source-domain-rules").status_code == 401
            # 伪造非 ADMIN_USERNAME 令牌应 401
            raw_client.cookies.set(COOKIE_NAME, wrong_token)
            assert raw_client.get("/api/sources").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_p1_ssrf_url_guard():
    assert check_safe_outbound_url("https://www.nasa.gov/feed/") is None
    assert check_safe_outbound_url("http://127.0.0.1:8000/admin") is not None
    assert check_safe_outbound_url("http://169.254.169.254/latest/meta-data/") is not None
    assert check_safe_outbound_url("http://10.0.0.5/internal") is not None
    assert check_safe_outbound_url("http://localhost:5433/") is not None
    assert check_safe_outbound_url("file:///etc/passwd") is not None
    # IPv4-mapped IPv6、纯十进制/十六进制及简写 IP 伪装同样被拦截
    assert check_safe_outbound_url("http://[::ffff:127.0.0.1]/") is not None
    assert check_safe_outbound_url("http://[::ffff:169.254.169.254]/latest/") is not None
    assert check_safe_outbound_url("http://2130706433/") is not None
    assert check_safe_outbound_url("http://0x7f000001/") is not None
    assert check_safe_outbound_url("http://127.1/") is not None


def test_review_ssrf_redirect_blocked_by_hook():
    import httpx
    import respx
    from app.services.fetch import fetch

    with respx.mock:
        respx.get("https://public.example.com/jump").mock(
            return_value=httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
            )
        )
        res = fetch("https://public.example.com/jump", max_attempts=2)
        assert res.error_kind == "http_4xx"
        assert "重定向目标被安全校验拒绝" in (res.error or "")
        assert res.attempts == 1  # 安全拒绝绝不重试


def test_review_sources_and_validate_one_block_internal_urls(client):
    r_create = client.post(
        "/api/sources",
        json={
            "code": "bad_internal_src",
            "name": "内网试探",
            "type": "rss",
            "level": "B",
            "url": "http://169.254.169.254/latest/meta-data/",
            "poll_interval_min": 30,
        },
    )
    assert r_create.status_code == 400

    r_val = client.post(
        "/api/sources/validate-one",
        json={"source_type": "rss", "url": "http://127.0.0.1:5433/"},
    )
    assert r_val.status_code == 400


def test_review_c_level_blank_evidence_and_xss_url_blocked(client, session, make_source):
    src = make_source(code="src_c_ev_guard", level="C")
    now = datetime.now(timezone.utc)
    raw = RawItem(
        source_id=src.id,
        url="https://weibo.com/u/111/clue",
        resolved_url="https://weibo.com/u/111/clue",
        publisher_url="https://weibo.com",
        publisher_name="微博",
        raw_title="网友拍到夜空神秘发光物",
        raw_summary="社交平台流传视频",
        full_text="社交平台流传视频",
        full_text_status="ok",
        published_at=now - timedelta(hours=1),
        fetched_at=now,
        url_hash="hash_c_ev_guard",
    )
    session.add(raw)
    session.flush()
    art = Article(
        raw_item_id=raw.id,
        title=raw.raw_title,
        publisher_name="微博",
        original_url=raw.url,
        source_level="C",
        source_level_reason="rule4:weibo.com",
        resolve_status="resolved",
        url_hash=raw.url_hash,
        first_seen_at=now,
        is_event=True,
        event_type="anomaly_psychic",
        content_nature="未证实目击",
        fact_anchor="社交平台流传未证实夜空发光物视频",
        title_final="夜空惊现三角光点，细思极恐？",
        body_lead="这是社交平台流传的夜空发光物视频，目前未经证实，属于网络讨论，别当真。",
        body_summary="一段拍摄于夜空的发光体视频在社交平台走红，由于缺乏权威来源与定位核验，有人猜测是不明飞行物（UFO），也有人认为是无人机编队，目前尚无可靠证据。",
        status="approved",
    )
    session.add(art)
    session.flush()

    # 1. 传纯空白补证不会写入空壳记录，发布仍被 Checklist 第 1 项拦截
    r_blank = client.patch(
        f"/api/articles/{art.id}",
        json={"evidence_url": "   ", "evidence_note": "   "},
    )
    assert r_blank.status_code == 200
    assert r_blank.json()["evidence_notes"] == []
    assert client.post(f"/api/articles/{art.id}/publish", json={}).status_code == 400

    # 2. 传 javascript: XSS 协议佐证链接返回 400
    r_xss = client.patch(
        f"/api/articles/{art.id}",
        json={"evidence_url": "javascript:alert(1)", "evidence_note": "恶意链接"},
    )
    assert r_xss.status_code == 400

    # 3. 写入合法外部佐证链接后可通过 Checklist 成功发布
    r_ok = client.patch(
        f"/api/articles/{art.id}",
        json={"evidence_url": "https://www.xinhuanet.com/verify/123", "evidence_note": "已交叉核验"},
    )
    assert r_ok.status_code == 200
    assert len(r_ok.json()["evidence_notes"]) == 1
    r_pub = client.post(f"/api/articles/{art.id}/publish", json={})
    assert r_pub.status_code == 201


def test_review_published_cannot_be_rejected_or_regenerated_before_unpublish(client, session, make_source):
    src = make_source(code="src_pub_lock", level="A")
    now = datetime.now(timezone.utc)
    raw = RawItem(
        source_id=src.id,
        url="https://www.nasa.gov/pub-lock",
        resolved_url="https://www.nasa.gov/pub-lock",
        publisher_url="https://www.nasa.gov",
        publisher_name="NASA",
        raw_title="NASA 发布近地小行星轨道更新",
        raw_summary="最新轨道观测确认安全飞掠。",
        full_text="最新轨道观测确认安全飞掠。",
        full_text_status="ok",
        published_at=now - timedelta(hours=1),
        fetched_at=now,
        url_hash="hash_pub_lock",
    )
    session.add(raw)
    session.flush()
    cluster = EventCluster(
        event_type="neo",
        primary_entity="2026 ZZ9",
        event_action="flyby",
        event_date=now.date(),
        canonical_title=raw.raw_title,
        first_seen_at=now,
        article_count=1,
        is_published=False,
    )
    session.add(cluster)
    session.flush()
    art = Article(
        raw_item_id=raw.id,
        title=raw.raw_title,
        publisher_name="NASA",
        original_url=raw.url,
        source_level="A",
        source_level_reason="rule2:nasa.gov",
        resolve_status="resolved",
        url_hash=raw.url_hash,
        first_seen_at=now,
        event_cluster_id=cluster.id,
        is_event=True,
        event_type="neo",
        content_nature="科学事实",
        fact_anchor="NASA 监测到小行星 2026 ZZ9 将近距离安全飞掠地球。",
        title_final="小行星贴脸地球！",
        body_lead="这颗编号 2026 ZZ9 的小行星会在近日近距离飞掠地球，最近距离很近，但目前没有撞击威胁。",
        body_summary="美国航天局（NASA）轨道监测确认近地小行星 2026 ZZ9 将在近日擦肩地球，目前轨道锁定准确，不存在撞击地球风险，天文爱好者可关注观测动态。",
        status="approved",
    )
    session.add(art)
    session.flush()

    pub = client.post(f"/api/articles/{art.id}/publish", json={}).json()
    # 已发布状态禁止直接 reject / to_pending / re_generate / re_preprocess
    for act in ("reject", "to_pending", "re_generate", "re_preprocess"):
        r = client.post(
            f"/api/articles/{art.id}/review",
            json={"action": act, "reject_reason": "测试原因"},
        )
        assert r.status_code == 400

    # 先下架 → cluster.is_published 同步校准为 False → 可正常重新上架
    assert client.post(f"/api/published/{pub['id']}/unpublish").status_code == 200
    session.refresh(cluster)
    assert cluster.is_published is False
    assert client.post(f"/api/published/{pub['id']}/republish").status_code == 200
    session.refresh(cluster)
    assert cluster.is_published is True
