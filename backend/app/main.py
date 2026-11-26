"""FastAPI 应用装配。

**API 进程不注册任何 APScheduler job、不发任何外部 HTTP**（04 §1.2）：
调度与采集全部由 `python -m app.worker` 里的另一个进程负责，这样
`uvicorn --reload` 不会重复注册 job 导致同一任务跑两遍。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api import auth, domain_rules, raw_items, sources
from app.core.config import settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import setup_logging

APP_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    version: str


def create_app() -> FastAPI:
    setup_logging()

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

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=APP_VERSION)

    return app


app = create_app()
