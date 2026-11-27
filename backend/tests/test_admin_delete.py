"""后台文章单删/批删、关键词规则删除与未收录域名清理 API 测试。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.models.keyword import KeywordRule
from app.models.pipeline import Article, RawItem
from app.models.publish import EventCluster, PublishedArticle
from app.models.source import UnknownDomain


def _seed_article(session, source_id: int, *, title: str, status: str = "pending") -> Article:
    uh = hashlib.sha256(f"{title}-{status}-{datetime.now(timezone.utc).timestamp()}".encode()).hexdigest()
    raw = RawItem(
        source_id=source_id,
        raw_title=title,
        url=f"https://example.com/{uh[:10]}",
        url_hash=uh,
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(raw)
    session.flush()
    art = Article(
        raw_item_id=raw.id,
        title=title,
        url_hash=uh,
        source_level="B",
        source_level_reason="test",
        status=status,
        title_final=title,
        body_lead="这是破题导语，说明基本事实来源与背景。",
        body_summary="这是大白话事实概要，交代谁做了什么以及结果如何，方便审核验证。",
    )
    session.add(art)
    session.flush()
    return art


def test_delete_and_batch_delete_articles(session, client: TestClient, make_source):
    src = make_source(code="del_test_src", name="测试源", level="B")
    pending_art = _seed_article(session, src.id, title="待定废稿A", status="pending")
    cand_art = _seed_article(session, src.id, title="候选测试B", status="candidate")
    pub_art = _seed_article(session, src.id, title="已发布文章C", status="published")

    # 绑定一个事件簇给 pending_art，验证删除文章后空簇一并清理
    cluster = EventCluster(
        event_type="deep_space_particle",
        canonical_title="待定废稿A",
        lead_article_id=pending_art.id,
        article_count=1,
    )
    session.add(cluster)
    session.flush()
    pending_art.event_cluster_id = cluster.id

    snap = PublishedArticle(
        article_id=pub_art.id,
        version=1,
        title_final="已发布文章C",
        body_lead="导语内容说明。",
        body_summary="正文摘要内容说明。",
        event_type="deep_space_particle",
        source_name="测试源",
        source_url="https://example.com/pub",
        source_level="B",
        is_active=True,
    )
    session.add(snap)
    session.flush()

    # 1. 单删 pending 成功并清理空事件簇
    r_del = client.delete(f"/api/articles/{pending_art.id}")
    assert r_del.status_code == 204
    assert session.get(Article, pending_art.id) is None
    assert session.get(EventCluster, cluster.id) is None

    # 2. 单删 published 返回 409
    r_pub = client.delete(f"/api/articles/{pub_art.id}")
    assert r_pub.status_code == 409

    # 3. 批量删除混合（1 条可删 + 1 条已发布 + 1 条不存在）
    r_batch = client.post(
        "/api/articles/batch-delete",
        json={"ids": [cand_art.id, pub_art.id, 999999]},
    )
    assert r_batch.status_code == 200
    data = r_batch.json()
    assert data["deleted_ids"] == [cand_art.id]
    skipped_ids = {s["id"] for s in data["skipped"]}
    assert skipped_ids == {pub_art.id, 999999}
    assert session.get(Article, cand_art.id) is None
    assert session.get(Article, pub_art.id) is not None


def test_delete_keyword_rule_and_unknown_domain(session, client: TestClient):
    rule = KeywordRule(
        type="signal",
        category="test_cat",
        pattern="测试信号词",
        weight=10,
        scope="title_summary",
        enabled=True,
    )
    session.add(rule)
    now = datetime.now(timezone.utc)
    unknown = UnknownDomain(
        domain="spam-noise.example",
        hit_count=3,
        first_seen_at=now,
        last_seen_at=now,
        sample_title="无关测试",
    )
    session.add(unknown)
    session.flush()

    r_kw = client.delete(f"/api/keyword-rules/{rule.id}")
    assert r_kw.status_code == 204
    assert session.get(KeywordRule, rule.id) is None

    r_unk = client.delete("/api/source-domain-rules/unknown/spam-noise.example")
    assert r_unk.status_code == 204
    assert client.delete("/api/source-domain-rules/unknown/spam-noise.example").status_code == 404
