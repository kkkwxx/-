"""敏感密钥加密存储与脱敏展示工具（Fernet 对称认证加密）。

设计原则：
- 使用基于 ``settings.JWT_SECRET`` SHA-256 派生的 32 字节密钥驱动 Fernet（AES-128-CBC + HMAC-SHA256）；
- 数据库仅存 ``enc:v1:<fernet_token>`` 密文；
- 所有 API 响应与审计日志只允许输出 ``mask_secret(...)`` 脱敏掩码，严禁回传明文。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "enc:v1:"


def _get_fernet() -> Fernet:
    raw_seed = (settings.JWT_SECRET or "dev-fallback-secret").encode("utf-8")
    digest = hashlib.sha256(raw_seed + b"::mystery-news-secret-v1").digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str | None) -> str | None:
    """加密非空密钥，返回带版本前缀的密文；空串返回 None。"""
    cleaned = (plaintext or "").strip()
    if not cleaned:
        return None
    token = _get_fernet().encrypt(cleaned.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(ciphertext: str | None) -> str:
    """解密密文，为空或损坏/密钥变更导致无法解密时返回空串。"""
    if not ciphertext:
        return ""
    if not ciphertext.startswith(_PREFIX):
        return ""
    token = ciphertext[len(_PREFIX) :].encode("ascii", errors="ignore")
    try:
        return _get_fernet().decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask_secret(plaintext: str | None) -> str | None:
    """生成不可逆脱敏掩码（如 ``sk-ws-****RayU9``）。"""
    cleaned = (plaintext or "").strip()
    if not cleaned:
        return None
    if len(cleaned) <= 8:
        return "****"
    if len(cleaned) <= 14:
        return f"{cleaned[:3]}****{cleaned[-3:]}"
    return f"{cleaned[:6]}****{cleaned[-4:]}"
