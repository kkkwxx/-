"""FastAPI 应用装配。

**API 进程不注册任何 APScheduler job、不发任何外部 HTTP**（04 §1.2）：
调度与采集全部由 `python -m app.worker` 里的另一个进程负责，这样
`uvicorn --reload` 不会重复注册 job 导致同一任务跑两遍。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api import (
    ai_providers,
    articles,
    auth,
    column,
    dashboard,
    domain_rules,
    keywords,
    policy,
    raw_items,
    schedule,
    sources,
)
from app.core.config import settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import setup_logging

APP_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    version: str


def _validate_runtime_security() -> None:
    """非 dev 环境启动时强制校验密钥与管理员密码哈希，防止默认凭据暴露。"""
    if settings.APP_ENV.lower() == "dev":
        return
    default_secret = "please-change-this-to-a-long-random-string"
    if not settings.JWT_SECRET or settings.JWT_SECRET == default_secret or len(settings.JWT_SECRET) < 24:
        raise RuntimeError("非 dev 环境必须配置长度 >= 24 的高熵随机 JWT_SECRET")
    if not (settings.ADMIN_PASSWORD_HASH or "").strip():
        raise RuntimeError("非 dev 环境必须配置 ADMIN_PASSWORD_HASH")


def create_app() -> FastAPI:
    setup_logging()
    _validate_runtime_security()

    app = FastAPI(title="神秘新闻 API", version=APP_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 统一错误格式 {code, message, detail}（技术栈 §5.1）
    app.add_exception_handler(AppError, app_error_handler)

    app.include_router(auth.router)
    app.include_router(sources.router)
    app.include_router(domain_rules.router)
    app.include_router(raw_items.router)
    app.include_router(articles.router)
    app.include_router(articles.published_router)
    app.include_router(column.router)
    app.include_router(keywords.router)
    app.include_router(policy.router)
    app.include_router(ai_providers.router)
    app.include_router(schedule.router)
    app.include_router(dashboard.router)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=APP_VERSION)

    return app


app = create_app()
