"""AI API 提供商管理 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VendorLiteral = Literal[
    "dashscope",
    "deepseek",
    "openai",
    "siliconflow",
    "gemini",
    "ollama",
    "custom",
]
RoleLiteral = Literal["primary", "fallback", "standby"]


class AiProviderPresetOut(BaseModel):
    vendor: VendorLiteral
    label: str
    base_url: str
    default_model_classify: str
    default_model_generate: str
    default_budget: int
    requires_key: bool
    note: str


class AiProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    vendor: str
    base_url: str
    has_api_key: bool = False
    api_key_mask: str | None = None
    model_classify: str
    model_generate: str
    daily_token_budget: int
    today_tokens: int = 0
    role: str
    enabled: bool
    note: str | None = None
    last_test_status: str | None = None
    last_test_at: datetime | None = None
    last_test_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AiProviderListResponse(BaseModel):
    items: list[AiProviderOut]
    presets: list[AiProviderPresetOut]
    env_fallback_configured: bool = False
    env_fallback_mask: str | None = None


class AiProviderCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    vendor: VendorLiteral = "custom"
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    model_classify: str = Field(min_length=1, max_length=64)
    model_generate: str = Field(min_length=1, max_length=64)
    daily_token_budget: int = Field(default=50_000, ge=0, le=100_000_000)
    role: RoleLiteral = "standby"
    enabled: bool = True
    note: str | None = None


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    vendor: VendorLiteral | None = None
    base_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = None
    clear_api_key: bool = False
    model_classify: str | None = Field(default=None, min_length=1, max_length=64)
    model_generate: str | None = Field(default=None, min_length=1, max_length=64)
    daily_token_budget: int | None = Field(default=None, ge=0, le=100_000_000)
    role: RoleLiteral | None = None
    enabled: bool | None = None
    note: str | None = None


class AiProviderRoleRequest(BaseModel):
    role: RoleLiteral


class AiProviderTestResponse(BaseModel):
    ok: bool
    latency_ms: int
    method: str
    detail: str
    tested_at: datetime
