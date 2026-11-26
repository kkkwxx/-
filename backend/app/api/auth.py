"""单管理员登录 / 登出 / 当前用户（03 §8.1）。

JWT 放 HttpOnly Cookie，前端拿不到 token 本体。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.api.deps import COOKIE_NAME, JWT_ALGORITHM, current_admin
from app.core.config import settings
from app.core.errors import UNAUTHORIZED, AppError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

NO_HASH_MESSAGE = "尚未配置 ADMIN_PASSWORD_HASH，无法登录；请在 .env 里设置 bcrypt 哈希"


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool = True
    username: str


class LogoutResponse(BaseModel):
    ok: bool = True
    message: str


class MeResponse(BaseModel):
    username: str


def _issue_token(username: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": username, "exp": expires},
        settings.JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def _verify_password(password: str) -> bool:
    """比对 bcrypt 哈希。哈希为空 → 一律失败（不允许空密码登录）。"""
    stored = (settings.ADMIN_PASSWORD_HASH or "").strip()
    if not stored:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    except (ValueError, TypeError):
        # 哈希格式非法（例如明文或截断）时按"校验失败"处理，不抛 500
        logger.warning("ADMIN_PASSWORD_HASH 不是合法的 bcrypt 哈希")
        return False


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response) -> LoginResponse:
    if not (settings.ADMIN_PASSWORD_HASH or "").strip():
        raise AppError(UNAUTHORIZED, NO_HASH_MESSAGE, status_code=401)
    if payload.username != settings.ADMIN_USERNAME or not _verify_password(
        payload.password
    ):
        raise AppError(UNAUTHORIZED, "用户名或密码不正确", status_code=401)

    token = _issue_token(payload.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.JWT_EXPIRE_MINUTES * 60,
        path="/",
    )
    return LoginResponse(ok=True, username=payload.username)


@router.post("/logout", response_model=LogoutResponse)
def logout(response: Response) -> LogoutResponse:
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return LogoutResponse(ok=True, message="已退出")


@router.get("/me", response_model=MeResponse)
def me(admin: str = current_admin) -> MeResponse:
    return MeResponse(username=admin)
