"""统一错误格式：{ "code", "message", "detail" }（技术栈 §5.1）。"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """业务错误。code 见 03-信源管理 §8.5。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}


# ---- 03 §8.5 错误码，集中定义避免拼错 ---- #
SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
SOURCE_CODE_DUPLICATE = "SOURCE_CODE_DUPLICATE"
SOURCE_HAS_DATA = "SOURCE_HAS_DATA"
BUILTIN_SOURCE = "BUILTIN_SOURCE"
READONLY_FIELD = "READONLY_FIELD"
MISSING_EXPECTED_DOMAINS = "MISSING_EXPECTED_DOMAINS"
INVALID_TYPE_FIELD = "INVALID_TYPE_FIELD"
DOMAIN_EXISTS = "DOMAIN_EXISTS"
INVALID_DOMAIN = "INVALID_DOMAIN"
RECALC_IN_PROGRESS = "RECALC_IN_PROGRESS"
UNAUTHORIZED = "UNAUTHORIZED"
VALIDATION_FAILED = "VALIDATION_FAILED"


def source_not_found(source_id: int) -> AppError:
    return AppError(
        SOURCE_NOT_FOUND,
        f"信源 {source_id} 不存在",
        status_code=404,
        detail={"source_id": source_id},
    )


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )
