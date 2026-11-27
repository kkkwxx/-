"""采集与 AI 调度控制 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ScheduleMode = Literal["manual", "window", "fixed_times"]
RunNowAction = Literal["full", "crawl_only", "ai_only"]


class ScheduleStateOut(BaseModel):
    mode: ScheduleMode
    crawl_enabled: bool
    ai_enabled: bool
    timezone: str
    weekdays: list[int]
    window_start: str
    window_end: str
    fixed_times: list[str]
    max_ai_items_per_run: int
    last_fixed_slot_key: str | None = None
    updated_by: str
    updated_at: datetime

    # 实时评估状态
    server_local_time: str
    crawl_active_now: bool
    ai_active_now: bool
    status_summary: str
    next_run_hint: str

    # 运行中任务与队列概览
    is_running_now: bool = False
    running_task_id: int | None = None
    running_stage: str | None = None
    queue_counts: dict[str, int] = Field(default_factory=dict)


class ScheduleUpdateRequest(BaseModel):
    mode: ScheduleMode | None = None
    crawl_enabled: bool | None = None
    ai_enabled: bool | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    weekdays: list[int] | None = None
    window_start: str | None = None
    window_end: str | None = None
    fixed_times: list[str] | None = None
    max_ai_items_per_run: int | None = Field(default=None, ge=1, le=100)


class RunNowRequest(BaseModel):
    action: RunNowAction = "full"
    max_items: int = Field(default=5, ge=1, le=50)
    source_ids: list[int] | None = None
    include_pending: bool = False


class RunNowResponse(BaseModel):
    task_run_id: int
    status: str
    action: RunNowAction
    max_items: int
    message: str


class ScheduleTaskRunItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    source_id: int | None = None
    status: str
    triggered_by: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats_json: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime


class ScheduleTaskRunListResponse(BaseModel):
    items: list[ScheduleTaskRunItem]
    total: int
