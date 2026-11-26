"""信源管理接口契约（03 §8）。全部走 TestClient，写操作鉴权被 conftest 覆盖。"""

from __future__ import annotations

import respx
from sqlalchemy import select

from app.models.pipeline import RawItem
from app.models.task import TaskRun


def _err(response) -> dict:
    body = response.json()
    assert {"code", "message", "detail"} <= set(body)
    return body


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #

def test_list_default_sort_by_severity(client, make_source):
    make_source(code="s_ok", health_status="ok")
    make_source(code="s_down", health_status="down")
    make_source(code="s_auth", health_status="auth_error")
    make_source(code="s_disabled", health_status="disabled", enabled=False)

    body = client.get("/api/sources").json()
    assert body["total"] == 4
    assert {"items", "total", "page", "page_size"} <= set(body)
    order = [item["code"] for item in body["items"]]
    assert order == ["s_down", "s_auth", "s_ok", "s_disabled"]


def test_list_filters_and_sort_whitelist(client, make_source):
    make_source(code="s_a", level="A", type="rss")
    make_source(code="s_b", level="B", type="google_news")
    assert client.get("/api/sources", params={"level": "A"}).json()["total"] == 1
    assert client.get("/api/sources", params={"type": "google_news"}).json()["total"] == 1
    # 非白名单 sort_by 回落到默认排序，不报错
    assert client.get("/api/sources", params={"sort_by": "drop table"}).status_code == 200


def test_summary_shape(client, make_source):
    make_source(code="s_ok")
    make_source(code="s_down", health_status="down")
    make_source(code="s_disabled", enabled=False, health_status="disabled")
    body = client.get("/api/sources/summary").json()
    assert body["total"] == 3
    assert body["needs_attention_count"] == 1
    assert body["disabled_count"] == 1
    assert "unknown_domain_count" in body


def test_detail_has_thresholds_and_derived_fields(client, make_source):
    source = make_source(code="s_detail")
    body = client.get(f"/api/sources/{source.id}").json()
    assert body["thresholds"] == {"degraded": 3, "down": 10, "starved": 3}
    assert body["key_configured"] is False
    assert body["etag_cached"] is False
    assert {"success_rate_7d", "items_7d", "empty_runs_7d", "p95_duration_ms_7d"} <= set(
        body["stats"]
    )


def test_unknown_source_is_404(client):
    response = client.get("/api/sources/999999")
    assert response.status_code == 404
    assert _err(response)["code"] == "SOURCE_NOT_FOUND"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

def test_patch_runtime_field_is_rejected(client, make_source):
    source = make_source(code="s_patch", consecutive_failures=9)
    response = client.patch(
        f"/api/sources/{source.id}",
        json={"consecutive_failures": 0, "etag": "x", "name": "改个名字"},
    )
    assert response.status_code == 400
    body = _err(response)
    assert body["code"] == "READONLY_FIELD"
    assert set(body["detail"]["rejected_fields"]) == {"consecutive_failures", "etag"}


def test_patch_writable_field_ok(client, make_source):
    source = make_source(code="s_patch2", consecutive_failures=7)
    response = client.patch(f"/api/sources/{source.id}", json={"name": "新名字"})
    assert response.status_code == 200
    assert response.json()["name"] == "新名字"
    assert response.json()["consecutive_failures"] == 7  # 故障不被抹掉


def test_create_a_level_requires_expected_domains(client):
    response = client.post(
        "/api/sources",
        json={"code": "t_a", "name": "A 源", "type": "rss", "level": "A",
              "url": "https://example.com/feed"},
    )
    assert response.status_code == 400
    assert _err(response)["code"] == "MISSING_EXPECTED_DOMAINS"


def test_create_returns_warning_when_not_validated(client):
    response = client.post(
        "/api/sources",
        json={"code": "t_new", "name": "新源", "type": "rss", "level": "B",
              "url": "https://example.com/feed"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["warning"]["code"] == "NOT_VALIDATED"
    assert body["last_validated_at"] is None


def test_create_duplicate_code(client, make_source):
    make_source(code="t_dup")
    response = client.post(
        "/api/sources",
        json={"code": "t_dup", "name": "重复", "type": "rss", "level": "B",
              "url": "https://example.com/f"},
    )
    assert response.status_code == 409
    assert _err(response)["code"] == "SOURCE_CODE_DUPLICATE"


def test_create_rss_with_query_template_is_invalid(client):
    response = client.post(
        "/api/sources",
        json={"code": "t_bad", "name": "x", "type": "rss", "level": "B",
              "url": "https://example.com/f", "query_template": "(a OR b)"},
    )
    assert response.status_code == 400
    assert _err(response)["code"] == "INVALID_TYPE_FIELD"


def test_delete_source_with_data_is_blocked(client, session, make_source):
    source = make_source(code="t_hasdata")
    session.add(
        RawItem(
            source_id=source.id, url="https://example.com/a", raw_title="标题",
            fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            url_hash="hash1",
        )
    )
    session.flush()
    response = client.delete(f"/api/sources/{source.id}")
    assert response.status_code == 409
    assert _err(response)["code"] == "SOURCE_HAS_DATA"


def test_delete_builtin_manual_is_blocked(client, make_source):
    source = make_source(code="manual")
    response = client.delete(f"/api/sources/{source.id}")
    assert response.status_code == 409
    assert _err(response)["code"] == "BUILTIN_SOURCE"


def test_delete_without_data_ok(client, make_source):
    source = make_source(code="t_del")
    assert client.delete(f"/api/sources/{source.id}").status_code == 204


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #

def test_toggle_off_then_on(client, make_source):
    source = make_source(code="t_toggle", consecutive_failures=4)
    off = client.post(f"/api/sources/{source.id}/toggle", json={"enabled": False}).json()
    assert off["health_status"] == "disabled"
    assert off["consecutive_failures"] == 4  # 停用期间保留

    on = client.post(f"/api/sources/{source.id}/toggle", json={"enabled": True}).json()
    assert on["health_status"] == "degraded"  # 重新启用不直接给 ok


def test_reset_health_keeps_counters(client, make_source):
    source = make_source(
        code="t_reset", consecutive_failures=9, health_status="down",
        last_error="boom", last_error_kind="connect",
        success_count=5, error_count=9,
    )
    body = client.post(f"/api/sources/{source.id}/reset-health").json()
    assert body["consecutive_failures"] == 0
    assert body["health_status"] == "ok"
    assert body["last_error"] is None and body["last_error_kind"] is None
    assert (body["success_count"], body["error_count"]) == (5, 9)


def test_poll_only_enqueues_task_and_never_calls_http(client, session, make_source):
    source = make_source(code="t_poll")
    with respx.mock(assert_all_called=False) as mock:
        response = client.post(f"/api/sources/{source.id}/poll")
        assert len(mock.calls) == 0  # API 进程绝不发外部 HTTP（04 §1.2）

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    run = session.execute(
        select(TaskRun).where(TaskRun.id == body["task_run_id"])
    ).scalar_one()
    assert (run.task_type, run.status, run.triggered_by) == ("poll", "pending", "manual")


def test_poll_disabled_source_rejected(client, make_source):
    source = make_source(code="t_poll_dis", enabled=False, health_status="disabled")
    response = client.post(f"/api/sources/{source.id}/poll")
    assert response.status_code == 400


def test_recalc_conflicts_when_running(client, session, make_source):
    source = make_source(code="t_recalc")
    session.add(
        TaskRun(task_type="recalc_levels", status="pending", triggered_by="manual")
    )
    session.flush()
    response = client.post("/api/sources/recalc-levels")
    assert response.status_code == 409
    assert _err(response)["code"] == "RECALC_IN_PROGRESS"


# --------------------------------------------------------------------------- #
# 域名规则
# --------------------------------------------------------------------------- #

def test_domain_rule_invalid_and_duplicate(client):
    bad = client.post("/api/source-domain-rules", json={"domain": "not a domain", "level": "A"})
    assert bad.status_code == 400
    assert _err(bad)["code"] in {"INVALID_DOMAIN", "VALIDATION_FAILED"}

    ok = client.post("/api/source-domain-rules", json={"domain": "example.com", "level": "B"})
    assert ok.status_code in (200, 201)
    assert ok.json()["recalc_suggested"] is True

    dup = client.post("/api/source-domain-rules", json={"domain": "example.com", "level": "A"})
    assert dup.status_code == 409
    assert _err(dup)["code"] == "DOMAIN_EXISTS"


def test_domain_rule_create_removes_unknown(client, session, make_source):
    from datetime import datetime, timezone

    from app.models.source import UnknownDomain

    session.add(
        UnknownDomain(
            domain="cast.org.cn", hit_count=3,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    assert client.post(
        "/api/source-domain-rules", json={"domain": "cast.org.cn", "level": "A"}
    ).status_code in (200, 201)
    session.flush()
    assert session.execute(select(UnknownDomain)).scalar_one_or_none() is None


def test_unknown_domain_report(client, session):
    from datetime import datetime, timezone

    from app.models.source import UnknownDomain

    now = datetime.now(timezone.utc)
    session.add(
        UnknownDomain(domain="ebiotrade.com", hit_count=5, first_seen_at=now, last_seen_at=now)
    )
    session.flush()
    body = client.get("/api/source-domain-rules/unknown").json()
    assert body["total"] == 1
    assert body["items"][0]["domain"] == "ebiotrade.com"
    assert body["items"][0]["hit_count"] == 5
