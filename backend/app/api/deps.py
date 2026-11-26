"""API 依赖：单管理员 JWT（HttpOnly Cookie）+ 审计写入 helper。"""

from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import UNAUTHORIZED, AppError
from app.models.audit import ReviewAction

# 03 §8.1：单管理员 JWT 存 HttpOnly Cookie
COOKIE_NAME = "mn_token"
JWT_ALGORITHM = "HS256"


def _decode(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload if isinstance(payload, dict) else None


def _username_from(payload: dict[str, Any]) -> str | None:
    name = payload.get("sub") or payload.get("username")
    return name if isinstance(name, str) and name else None


def require_admin(request: Request) -> str:
    """写操作依赖：校验 Cookie 里的 JWT，失败抛 401。"""
    payload = _decode(request.cookies.get(COOKIE_NAME))
    if payload is None:
        raise AppError(UNAUTHORIZED, "未登录或登录已过期", status_code=401)
    username = _username_from(payload)
    if not username:
        raise AppError(UNAUTHORIZED, "令牌缺少用户名", status_code=401)
    return username


# 路由里直接用 `admin: str = current_admin`
current_admin: str = Depends(require_admin)


def current_admin_optional(request: Request) -> str:
    """不校验，只用于 review_action 记录操作人（03 §7.4）。"""
    payload = _decode(request.cookies.get(COOKIE_NAME))
    if payload is None:
        return "system"
    return _username_from(payload) or "system"


def write_review_action(
    session: Session,
    *,
    reviewer: str,
    action: str,
    source_id: int | None = None,
    article_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    """写一条 review_action。调用方负责 commit（同事务）。"""
    session.add(
        ReviewAction(
            reviewer=reviewer or "system",
            action=action,
            source_id=source_id,
            article_id=article_id,
            before_json=before,
            after_json=after,
            note=note,
        )
    )
