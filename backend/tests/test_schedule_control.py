"""采集与 AI 调度策略（纯手动 / 活跃时间窗 / 固定时间点 / 立即执行防重入）测试。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.models.schedule import PipelineScheduleConfig
from app.services import scheduler_control


def _bj_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """将北京时间转为 UTC aware datetime，便于测试时间窗与定点逻辑。"""
    from datetime import timedelta

    return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=8))).astimezone(
        timezone.utc
    )


def test_evaluate_manual_and_window_modes():
    cfg = PipelineScheduleConfig(
        mode="manual",
        crawl_enabled=True,
        ai_enabled=True,
        timezone="Asia/Shanghai",
        weekdays=[1, 2, 3, 4, 5, 6, 7],
        window_start="09:00",
        window_end="22:00",
        fixed_times=["09:00", "14:00", "20:00"],
        max_ai_items_per_run=5,
    )
    ev_manual = scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 15, 0))
    assert ev_manual.crawl_active_now is False
    assert ev_manual.ai_active_now is False

    # 常规时间窗 09:00 - 22:00（2026-11-27 是周五，weekday=5）
    cfg.mode = "window"
    ev_in = scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 15, 0))
    assert ev_in.crawl_active_now is True
    assert ev_in.ai_active_now is True

    ev_out = scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 23, 15))
    assert ev_out.crawl_active_now is False
    assert ev_out.ai_active_now is False

    # 独立关闭 AI 开关，仅允许自动采集
    cfg.ai_enabled = False
    ev_crawl_only = scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 15, 0))
    assert ev_crawl_only.crawl_active_now is True
    assert ev_crawl_only.ai_active_now is False

    # 跨午夜时间窗 22:00 - 06:00
    cfg.ai_enabled = True
    cfg.window_start = "22:00"
    cfg.window_end = "06:00"
    assert scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 23, 30)).ai_active_now is True
    assert scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 2, 15)).ai_active_now is True
    assert scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 12, 0)).ai_active_now is False

    # 非允许星期静默
    cfg.weekdays = [1, 2, 3]  # 周五不在内
    assert scheduler_control.evaluate_schedule(cfg, now=_bj_utc(2026, 11, 27, 23, 30)).crawl_active_now is False


def test_fixed_times_slot_idempotency(session):
    cfg = scheduler_control.get_or_create_schedule(session)
    cfg.mode = "fixed_times"
    cfg.crawl_enabled = True
    cfg.ai_enabled = True
    cfg.fixed_times = ["09:00", "20:00"]
    session.flush()

    t_hit = _bj_utc(2026, 11, 27, 9, 0)
    slot1, _ = scheduler_control.claim_due_fixed_slot(session, now=t_hit)
    assert slot1 == "2026-11-27 09:00"

    # 同时间槽内二次轮询必须幂等跳过
    slot2, _ = scheduler_control.claim_due_fixed_slot(session, now=_bj_utc(2026, 11, 27, 9, 1))
    assert slot2 is None

    # 非定点时刻不触发
    slot3, _ = scheduler_control.claim_due_fixed_slot(session, now=_bj_utc(2026, 11, 27, 10, 0))
    assert slot3 is None


def test_schedule_api_crud_and_run_now_conflict(session, client: TestClient, monkeypatch):
    # 1. 默认初始化为纯手动模式
    r0 = client.get("/api/schedule")
    assert r0.status_code == 200
    assert r0.json()["mode"] == "manual"
    assert r0.json()["crawl_active_now"] is False
    assert r0.json()["ai_active_now"] is False

    # 2. 更新为活跃时间窗
    r1 = client.put(
        "/api/schedule",
        json={
            "mode": "window",
            "crawl_enabled": True,
            "ai_enabled": False,
            "weekdays": [1, 2, 3, 4, 5],
            "window_start": "08:30",
            "window_end": "21:30",
            "max_ai_items_per_run": 8,
        },
    )
    assert r1.status_code == 200
    body = r1.json()
    assert body["mode"] == "window"
    assert body["ai_enabled"] is False
    assert body["window_start"] == "08:30"
    assert body["max_ai_items_per_run"] == 8

    # 3. 非法时间格式拦截
    r_bad = client.put("/api/schedule", json={"window_start": "25:99"})
    assert r_bad.status_code == 400

    # 4. 立即执行：测试里关闭线程启动防止访问外网，验证 202 及并发 409 防重入
    orig_start = scheduler_control.start_pipeline_run

    def _no_thread_start(*args, **kwargs):
        kwargs["spawn_thread"] = False
        return orig_start(*args, **kwargs)

    monkeypatch.setattr("app.api.schedule.start_pipeline_run", _no_thread_start)

    r_run = client.post(
        "/api/schedule/run-now",
        json={"action": "crawl_only", "max_items": 3, "include_pending": False},
    )
    assert r_run.status_code == 202
    task_id = r_run.json()["task_run_id"]
    assert task_id > 0

    # 再次点击应报 409 冲突防重入
    r_dup = client.post(
        "/api/schedule/run-now",
        json={"action": "full", "max_items": 5},
    )
    assert r_dup.status_code == 409

    # 流水列表可见刚建任务
    r_runs = client.get("/api/schedule/runs", params={"task_type": "pipeline_run"})
    assert r_runs.status_code == 200
    assert any(item["id"] == task_id for item in r_runs.json()["items"])
