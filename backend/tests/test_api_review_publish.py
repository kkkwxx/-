"""审核工作台、不可变发布快照、公开栏目与运营监控 API 集成测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.pipeline import Article, RawItem
from app.models.publish import EventCluster


def _seed_candidate(session, make_source, *, level: str = "A", event_type: str = "deep_space_particle", content_nature: str = "科学事实"):
    src = make_source(code=f"src_rev_{level}_{event_type}", level=level)
    now = datetime.now(timezone.utc)
    raw = RawItem(
        source_id=src.id,
        url=f"https://www.nasa.gov/news/{event_type}",
        resolved_url=f"https://www.nasa.gov/news/{event_type}",
        publisher_url="https://www.nasa.gov",
        publisher_name="NASA",
        raw_title="江门中微子实验发布首个物理成果",
        raw_summary="实验团队发布首个高精度中微子振荡参数成果。",
        full_text="实验团队发布首个高精度中微子振荡参数成果。",
        full_text_status="ok",
        published_at=now - timedelta(hours=2),
        fetched_at=now,
        url_hash=f"hash_{level}_{event_type}",
    )
    session.add(raw)
    session.flush()

    cluster = EventCluster(
        event_type=event_type,
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
        source_level=level,
        source_level_reason="rule2:nasa.gov",
        resolve_status="resolved",
        url_hash=raw.url_hash,
        first_seen_at=now,
        event_cluster_id=cluster.id,
        is_event=True,
        event_type=event_type,
        content_nature=content_nature,
        event_confidence=0.95,
        fact_anchor="江门中微子实验发布首个物理成果，测得关键中微子数据。",
        title_candidates=[
            {"title": "科学家抓到“鬼”了？", "style_tag": "恐怖片式", "hook_score": 5, "passed_check": True, "check_errors": []}
        ],
        title_final="科学家抓到“鬼”了？",
        body_lead="别怕，这里的“鬼”是几乎不跟物质反应的中微子，也叫“幽灵粒子”。中国江门中微子实验这次测到了关键数据。",
        body_summary="位于广东地下700米的江门中微子实验（专门捕捉幽灵粒子的巨型探测器）发布首个物理成果，高精度测得中微子振荡参数，为破解宇宙物质起源迈出关键一步。",
        hook_score=5,
        style_tag="恐怖片式",
        review_priority=55,
        status="candidate" if level in ("A", "B") else "pending",
    )
    session.add(art)
    session.flush()
    cluster.lead_article_id = art.id
    session.flush()
    return art


def test_review_edit_publish_unpublish_and_column_flow(client, session, make_source):
    art = _seed_candidate(session, make_source)

    # 1. 列表查询
    r = client.get("/api/articles", params={"status": "candidate"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == art.id

    # 2. 详情含 7 项 Checklist
    detail = client.get(f"/api/articles/{art.id}").json()
    assert len(detail["publish_checklist"]) == 7
    assert all(item["passed"] for item in detail["publish_checklist"])
    assert detail["can_publish"] is False  # 尚未人工 approve

    # 3. 编辑加入红线词会即时触发轻校验失败并阻止 approve
    bad_edit = client.patch(
        f"/api/articles/{art.id}",
        json={"title_final": "科学家宣布外星人已确认降临！"},
    )
    assert bad_edit.status_code == 200
    assert bad_edit.json()["verification"]["hard_failed"] is True
    bad_approve = client.post(f"/api/articles/{art.id}/review", json={"action": "approve"})
    assert bad_approve.status_code == 400

    # 4. 改回合规标题并补充文化标签 → 通过审核 → 发布快照
    ok_edit = client.patch(
        f"/api/articles/{art.id}",
        json={"title_final": "科学家抓到“鬼”了？", "culture_tag": "玄幽·水"},
    )
    assert ok_edit.status_code == 200
    assert ok_edit.json()["verification"]["passed"] is True

    approved = client.post(f"/api/articles/{art.id}/review", json={"action": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["can_publish"] is True

    pub_resp = client.post(f"/api/articles/{art.id}/publish", json={})
    assert pub_resp.status_code == 201
    pub = pub_resp.json()
    assert pub["title_final"] == "科学家抓到“鬼”了？"
    assert pub["is_active"] is True
    assert pub["version"] == 1

    # 5. 公开栏目接口可读发布快照
    col_list = client.get("/api/column/articles")
    assert col_list.status_code == 200
    assert col_list.json()["total"] == 1
    col_detail = client.get(f"/api/column/articles/{pub['id']}")
    assert col_detail.status_code == 200
    assert col_detail.json()["source_level"] == "A"

    # 6. 修改已发布文章只退回草稿审核，不篡改线上快照
    client.patch(f"/api/articles/{art.id}", json={"title_final": "地下700米逮到幽灵粒子！"})
    still_old = client.get(f"/api/column/articles/{pub['id']}").json()
    assert still_old["title_final"] == "科学家抓到“鬼”了？"

    # 7. 下架与重发
    unpub = client.post(f"/api/published/{pub['id']}/unpublish")
    assert unpub.status_code == 200
    assert unpub.json()["is_active"] is False
    assert client.get("/api/column/articles").json()["total"] == 0

    repub = client.post(f"/api/published/{pub['id']}/republish")
    assert repub.status_code == 200
    assert repub.json()["is_active"] is True

    # 8. Dashboard 总览
    dash = client.get("/api/dashboard/overview")
    assert dash.status_code == 200
    assert dash.json()["active_published_total"] == 1


def test_keyword_rules_crud_api(client):
    created = client.post(
        "/api/keyword-rules",
        json={"type": "signal", "category": "signal", "pattern": "虫洞|平行宇宙", "weight": 3},
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    listed = client.get("/api/keyword-rules", params={"type": "signal"})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    toggled = client.patch(f"/api/keyword-rules/{rule_id}", json={"enabled": False})
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False
