"""公开栏目综合神秘热度榜、搜索与浏览量自增测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models.pipeline import Article, RawItem
from app.models.publish import EventCluster, PublishedArticle
from app.services.ranking import compute_hot_score, compute_rank_badge


def _make_published(
    db,
    source_id: int,
    *,
    title: str,
    event_type: str = "deep_space_particle",
    source_name: str = "NASA",
    source_level: str = "A",
    hook_score: int = 4,
    view_count: int = 0,
    published_at: datetime | None = None,
    cluster_count: int = 1,
    body_lead: str = "这是一条完整的破题导语说明。",
    body_summary: str = "这是一段清晰的事实概要正文，包含可核验来源细节。",
) -> PublishedArticle:
    url_hash = hashlib.sha256(f"{title}-{published_at}".encode("utf-8")).hexdigest()
    raw = RawItem(
        source_id=source_id,
        raw_title=title,
        url=f"https://www.nasa.gov/news/{url_hash[:12]}",
        url_hash=url_hash,
        fetched_at=published_at or datetime.now(timezone.utc),
    )
    db.add(raw)
    db.flush()

    art = Article(
        raw_item_id=raw.id,
        title=title,
        url_hash=url_hash,
        source_level=source_level,
        source_level_reason="test",
        event_type=event_type,
        status="published",
        title_final=title,
        body_lead=body_lead,
        body_summary=body_summary,
        hook_score=hook_score,
    )
    db.add(art)
    db.flush()

    cluster_id = None
    if cluster_count > 1:
        cluster = EventCluster(
            event_type=event_type,
            canonical_title=title,
            lead_article_id=art.id,
            article_count=cluster_count,
            is_published=True,
        )
        db.add(cluster)
        db.flush()
        cluster_id = cluster.id

    snap = PublishedArticle(
        article_id=art.id,
        event_cluster_id=cluster_id,
        version=1,
        title_final=title,
        body_lead=body_lead,
        body_summary=body_summary,
        event_type=event_type,
        source_name=source_name,
        source_url=raw.url,
        source_level=source_level,
        hook_score=hook_score,
        view_count=view_count,
        is_active=True,
        published_at=published_at or datetime.now(timezone.utc),
    )
    db.add(snap)
    db.flush()
    db.refresh(snap)
    return snap


def test_compute_hot_score_factors():
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    base = SimpleNamespace(
        hook_score=3, source_level="B", view_count=0, published_at=now
    )
    high_hook = SimpleNamespace(
        hook_score=5, source_level="B", view_count=0, published_at=now
    )
    level_a = SimpleNamespace(
        hook_score=3, source_level="A", view_count=0, published_at=now
    )
    viewed = SimpleNamespace(
        hook_score=3, source_level="B", view_count=10, published_at=now
    )
    older = SimpleNamespace(
        hook_score=3,
        source_level="B",
        view_count=0,
        published_at=now - timedelta(days=5),
    )

    s_base = compute_hot_score(base, cluster_article_count=1, now=now)
    assert compute_hot_score(high_hook, cluster_article_count=1, now=now) > s_base
    assert compute_hot_score(level_a, cluster_article_count=1, now=now) > s_base
    assert compute_hot_score(base, cluster_article_count=3, now=now) > s_base
    assert compute_hot_score(viewed, cluster_article_count=1, now=now) > s_base
    assert compute_hot_score(older, cluster_article_count=1, now=now) < s_base

    assert compute_rank_badge(base, cluster_article_count=3, now=now) == "爆"
    assert compute_rank_badge(base, cluster_article_count=1, now=now) == "新"
    assert compute_rank_badge(older, cluster_article_count=1, now=now) is None
    assert (
        compute_rank_badge(
            SimpleNamespace(
                hook_score=3,
                source_level="A",
                view_count=0,
                published_at=now - timedelta(days=5),
            ),
            cluster_article_count=1,
            now=now,
        )
        == "A源"
    )


def test_column_search_sort_ranking_and_view_increment(
    session, client: TestClient, make_source
):
    src = make_source(code="col_rank_src", name="NASA", level="A")
    now = datetime.now(timezone.utc)
    s1 = _make_published(
        session,
        src.id,
        title="詹姆斯韦伯望远镜捕捉神秘深空信号",
        event_type="deep_space_particle",
        source_name="NASA",
        source_level="A",
        hook_score=5,
        cluster_count=3,
        published_at=now - timedelta(hours=4),
    )
    s2 = _make_published(
        session,
        src.id,
        title="2026年近地小行星将安全掠过地月轨道",
        event_type="neo",
        source_name="ESA",
        source_level="B",
        hook_score=3,
        cluster_count=1,
        published_at=now - timedelta(hours=1),
    )
    s3 = _make_published(
        session,
        src.id,
        title="脑机接口实验读取100%模拟词组",
        event_type="bci_consciousness",
        source_name="Nature",
        source_level="A",
        hook_score=4,
        cluster_count=1,
        published_at=now - timedelta(hours=2),
    )

    # 1. 默认热门排序：s1（A源+hook5+3源共报）应排第一，且 counts_by_type 准确
    r_hot = client.get("/api/column/articles", params={"sort": "hot"})
    assert r_hot.status_code == 200
    body = r_hot.json()
    assert body["total"] == 3
    assert body["total_active"] == 3
    assert body["items"][0]["id"] == s1.id
    assert body["items"][0]["cluster_article_count"] == 3
    assert body["items"][0]["rank_badge"] == "爆"
    assert body["counts_by_type"]["neo"] == 1

    # 2. 最新时间排序：s2 最新，应排第一
    r_latest = client.get("/api/column/articles", params={"sort": "latest"})
    assert r_latest.status_code == 200
    assert r_latest.json()["items"][0]["id"] == s2.id

    # 3. 关键词搜索 + LIKE 通配符安全转义（搜 "%" 只匹配含字面 "%" 的 s3，不匹配全部）
    r_search = client.get("/api/column/articles", params={"q": "小行星"})
    assert r_search.status_code == 200
    assert [i["id"] for i in r_search.json()["items"]] == [s2.id]

    r_pct = client.get("/api/column/articles", params={"q": "100%"})
    assert r_pct.status_code == 200
    assert [i["id"] for i in r_pct.json()["items"]] == [s3.id]

    # 4. A 级源筛选与高悬念筛选
    r_a = client.get("/api/column/articles", params={"source_level": "A"})
    assert r_a.status_code == 200
    assert {i["id"] for i in r_a.json()["items"]} == {s1.id, s3.id}

    r_hook = client.get("/api/column/articles", params={"min_hook_score": 5})
    assert r_hook.status_code == 200
    assert [i["id"] for i in r_hook.json()["items"]] == [s1.id]

    # 5. 右侧神秘热榜 + 点击详情页自动递增 view_count 并抬升热度
    r_rank1 = client.get("/api/column/ranking", params={"limit": 5})
    assert r_rank1.status_code == 200
    rank_items = r_rank1.json()["items"]
    assert len(rank_items) == 3
    assert rank_items[0]["id"] == s1.id
    assert rank_items[0]["rank"] == 1

    # 连续打开 s2 详情页 15 次，s2 浏览量和热度显著提升并反超 s3
    for expected_vc in range(1, 16):
        r_det = client.get(f"/api/column/articles/{s2.id}")
        assert r_det.status_code == 200
        assert r_det.json()["view_count"] == expected_vc

    r_rank2 = client.get("/api/column/ranking", params={"limit": 5})
    assert r_rank2.status_code == 200
    ids_after = [i["id"] for i in r_rank2.json()["items"]]
    assert ids_after.index(s2.id) < ids_after.index(s3.id)
