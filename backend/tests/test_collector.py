"""健康状态回写（03 §7）与采集落地（04 §10）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.pipeline import Article, RawItem
from app.models.source import UnknownDomain
from app.services.collector import persist_items
from app.services.dto import FetchResult, RawItemDraft
from app.services.health import apply_poll_result, consecutive_empty_runs, thresholds

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _fail(session, source, *, kind: str = "connect", times: int = 1) -> None:
    for _ in range(times):
        apply_poll_result(
            session, source, ok=False, error_kind=kind, error="boom", fetched=0, now=NOW
        )


def _empty_success(session, source, *, times: int = 1) -> None:
    for _ in range(times):
        apply_poll_result(session, source, ok=True, error_kind=None, error=None, fetched=0, now=NOW)


# --------------------------------------------------------------------------- #
# 阈值与任务级计数
# --------------------------------------------------------------------------- #

def test_thresholds_are_exposed():
    assert thresholds() == {"degraded": 3, "down": 10, "starved": 3}


def test_single_failure_keeps_ok(session, make_source):
    source = make_source()
    _fail(session, source)
    assert source.consecutive_failures == 1
    assert source.health_status == "ok"  # 阈值未到
    assert source.last_error_kind == "connect"
    assert source.last_poll_at == NOW


def test_three_failures_degrade_then_ten_down(session, make_source):
    source = make_source()
    _fail(session, source, times=3)
    assert source.health_status == "degraded"
    _fail(session, source, times=7)
    assert source.consecutive_failures == 10
    assert source.health_status == "down"
    assert source.error_count == 10


def test_success_resets_failures(session, make_source):
    source = make_source()
    _fail(session, source, times=5)
    apply_poll_result(session, source, ok=True, error_kind=None, error=None, fetched=3, now=NOW)
    assert source.consecutive_failures == 0
    assert source.health_status == "ok"
    assert source.success_count == 1
    assert source.last_error is None
    assert source.last_success_at == NOW


def test_auth_error_is_immediate_and_not_counted(session, make_source):
    source = make_source()
    _fail(session, source, kind="auth")
    assert source.health_status == "auth_error"
    assert source.consecutive_failures == 0  # auth 不适用任务级计数（04 §13.1）
    assert source.error_count == 1


def test_task_level_counting_not_request_level(session, make_source):
    """一次任务失败只 +1，不按 HTTP 请求数累加（03 §7.3）。"""
    source = make_source()
    _fail(session, source)
    assert source.consecutive_failures == 1


# --------------------------------------------------------------------------- #
# starved（空转）
# --------------------------------------------------------------------------- #

def test_three_empty_successes_make_starved(session, make_source, make_task_run):
    source = make_source()
    for _ in range(3):
        make_task_run(source, status="success", fetched=0)
    _empty_success(session, source)
    assert source.health_status == "starved"
    assert source.last_error_kind == "empty_feed"
    # 空转仍算成功，不清空 last_success_at
    assert source.last_success_at == NOW


def test_starved_recovers_on_one_item(session, make_source, make_task_run):
    source = make_source(health_status="starved")
    for _ in range(3):
        make_task_run(source, status="success", fetched=0)
    apply_poll_result(session, source, ok=True, error_kind=None, error=None, fetched=1, now=NOW)
    assert source.health_status == "ok"


def test_starved_still_accumulates_failures(session, make_source):
    """starved 不豁免失败累计（03 §7.7 第 2 条）。"""
    source = make_source(health_status="starved")
    _fail(session, source, times=3)
    assert source.health_status == "degraded"


def test_consecutive_empty_runs_requires_task_history(session, make_source, make_task_run):
    source = make_source()
    assert consecutive_empty_runs(session, source.id) is False
    make_task_run(source, status="success", fetched=0)
    make_task_run(source, status="success", fetched=0)
    assert consecutive_empty_runs(session, source.id) is False
    make_task_run(source, status="success", fetched=0)
    assert consecutive_empty_runs(session, source.id) is True


# --------------------------------------------------------------------------- #
# 落地与入口去重
# --------------------------------------------------------------------------- #

def _draft(source_id: int, **kwargs) -> RawItemDraft:
    defaults = dict(
        source_id=source_id,
        url="https://www.nasa.gov/a/",
        title="NASA 发现近地小行星",
        fetched_at=NOW,
        resolved_url="https://www.nasa.gov/a/",
        published_at=NOW - timedelta(hours=1),
    )
    defaults.update(kwargs)
    return RawItemDraft(**defaults)


def test_persist_grades_and_sets_full_text_status(session, make_source):
    source = make_source(code="s_persist", level="A", expected_domains=["nasa.gov"])
    session.add_all([])
    result = FetchResult(
        items=[
            _draft(source.id, url="https://www.nasa.gov/a/", content_encoded="<p>全文</p>"),
            _draft(source.id, url="https://www.nasa.gov/b/", resolved_url="https://www.nasa.gov/b/"),
        ]
    )
    stats = persist_items(session, source, result, now=NOW)
    session.flush()

    assert stats["inserted"] == 2
    rows = session.execute(select(RawItem).order_by(RawItem.id)).scalars().all()
    statuses = {row.url: row.full_text_status for row in rows}
    assert statuses["https://www.nasa.gov/a/"] == "skipped"  # RSS 自带全文
    assert statuses["https://www.nasa.gov/b/"] == "pending"

    articles = session.execute(select(Article).order_by(Article.id)).scalars().all()
    assert {a.source_level for a in articles} == {"A"}
    assert all(a.status == "ingested" for a in articles)
    assert all(a.resolve_status == "resolved" for a in articles)


def test_persist_skips_old_items(session, make_source):
    source = make_source(code="s_old")
    result = FetchResult(
        items=[_draft(source.id, published_at=NOW - timedelta(days=30))]
    )
    stats = persist_items(session, source, result, now=NOW)
    assert stats["skipped_old"] == 1
    assert stats["inserted"] == 0


def test_persist_dedupes_and_records_dup_sources(session, make_source):
    source_a = make_source(code="s_dup_a")
    source_b = make_source(code="s_dup_b")
    item = _draft(source_a.id)
    persist_items(session, source_a, FetchResult(items=[item]), now=NOW)
    session.flush()

    stats = persist_items(
        session,
        source_b,
        FetchResult(items=[_draft(source_b.id)]),
        now=NOW,
    )
    session.flush()
    assert stats["skipped_dup"] == 1

    article = session.execute(select(Article)).scalar_one()
    assert source_b.id in article.dup_sources
    assert session.execute(select(func.count()).select_from(RawItem)).scalar() == 1


def test_rule6_upserts_unknown_domain(session, make_source):
    source = make_source(code="s_unknown", level="A", expected_domains=["fnal.gov"])
    draft = _draft(
        source.id,
        url="https://www.bhpioneer.com/x/",
        resolved_url="https://www.bhpioneer.com/x/",
        title="转载：中微子新进展",
    )
    persist_items(session, source, FetchResult(items=[draft]), now=NOW)
    session.flush()

    row = session.execute(select(UnknownDomain)).scalar_one()
    assert row.domain == "bhpioneer.com"
    assert row.hit_count == 1
    assert row.sample_source_code == "s_unknown"

    persist_items(
        session,
        source,
        FetchResult(items=[_draft(source.id, url="https://www.bhpioneer.com/y/",
                                   resolved_url="https://www.bhpioneer.com/y/")]),
        now=NOW,
    )
    session.flush()
    assert session.execute(select(UnknownDomain)).scalar_one().hit_count == 2
