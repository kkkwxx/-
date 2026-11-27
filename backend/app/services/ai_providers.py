"""AI API 提供商调度服务：预设模板、.env 自动导入、主备解析与连通性测试。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.secrets import decrypt_secret, encrypt_secret, mask_secret
from app.models.ai import AiRun
from app.models.api_provider import AiProviderConfig
from app.services.fetch import check_safe_outbound_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderPreset:
    vendor: str
    label: str
    base_url: str
    default_model_classify: str
    default_model_generate: str
    default_budget: int
    requires_key: bool
    note: str


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        vendor="dashscope",
        label="阿里百炼 (DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model_classify="qwen-flash",
        default_model_generate="qwen-plus",
        default_budget=50_000,
        requires_key=True,
        note="推荐：qwen-flash 分类低成本 + qwen-plus 悬念文案生成",
    ),
    ProviderPreset(
        vendor="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model_classify="deepseek-chat",
        default_model_generate="deepseek-chat",
        default_budget=100_000,
        requires_key=True,
        note="OpenAI 兼容接口，性价比高",
    ),
    ProviderPreset(
        vendor="siliconflow",
        label="硅基流动 (SiliconFlow)",
        base_url="https://api.siliconflow.cn/v1",
        default_model_classify="Qwen/Qwen2.5-7B-Instruct",
        default_model_generate="Qwen/Qwen2.5-32B-Instruct",
        default_budget=100_000,
        requires_key=True,
        note="国内多模型聚合，适合作为免费/低价备用降级源",
    ),
    ProviderPreset(
        vendor="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model_classify="gpt-4o-mini",
        default_model_generate="gpt-4o",
        default_budget=50_000,
        requires_key=True,
        note="标准 OpenAI API",
    ),
    ProviderPreset(
        vendor="gemini",
        label="Google Gemini (OpenAI 兼容)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model_classify="gemini-2.5-flash",
        default_model_generate="gemini-2.5-pro",
        default_budget=100_000,
        requires_key=True,
        note="Google AI Studio OpenAI 兼容入口",
    ),
    ProviderPreset(
        vendor="ollama",
        label="本地 Ollama（免 Key）",
        base_url="http://127.0.0.1:11434/v1",
        default_model_classify="qwen2.5:7b",
        default_model_generate="qwen2.5:14b",
        default_budget=0,
        requires_key=False,
        note="本地部署开源模型，0 外部额度消耗（预算填 0 表示不限）",
    ),
    ProviderPreset(
        vendor="custom",
        label="自定义 OpenAI 兼容网关",
        base_url="",
        default_model_classify="",
        default_model_generate="",
        default_budget=50_000,
        requires_key=True,
        note="任意支持 /chat/completions 的自建或第三方网关",
    ),
)


@dataclass(frozen=True)
class RuntimeProvider:
    provider_id: int | None
    code: str
    name: str
    vendor: str
    base_url: str
    api_key: str
    model_classify: str
    model_generate: str
    daily_token_budget: int
    role: str


def validate_provider_base_url(vendor: str, base_url: str) -> str | None:
    """校验提供商 Base URL：仅允许 http/https；非 ollama 强制走 SSRF 外网检查。"""
    cleaned = (base_url or "").strip()
    if not cleaned:
        return "Base URL 不能为空"
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Base URL 必须是合法的 http:// 或 https:// 地址"
    host = parsed.hostname.lower()
    if host in ("169.254.169.254", "metadata.google.internal"):
        return "禁止访问云元数据地址"
    if vendor == "ollama":
        # 允许本地 Ollama 回环地址，但仍拒绝非 http(s) 与云元数据
        return None
    return check_safe_outbound_url(cleaned)


def ensure_default_provider_seeded(db: Session) -> bool:
    """表为空且 .env 已配置 DASHSCOPE_API_KEY 时，自动导入为首条主用记录。"""
    count = int(
        db.execute(select(func.count()).select_from(AiProviderConfig)).scalar_one() or 0
    )
    if count > 0:
        return False
    env_key = (settings.DASHSCOPE_API_KEY or "").strip()
    if not env_key:
        return False

    row = AiProviderConfig(
        code="dashscope_env_default",
        name="阿里百炼（.env 自动导入）",
        vendor="dashscope",
        base_url=settings.DASHSCOPE_BASE_URL,
        api_key_encrypted=encrypt_secret(env_key),
        api_key_mask=mask_secret(env_key),
        model_classify=settings.LLM_MODEL_CLASSIFY,
        model_generate=settings.LLM_MODEL_GENERATE,
        daily_token_budget=int(settings.LLM_DAILY_TOKEN_BUDGET or 50_000),
        role="primary",
        enabled=True,
        note="由本地 .env 中的 DASHSCOPE_API_KEY 自动初始化，可直接在此修改模型或预算。",
    )
    db.add(row)
    db.commit()
    return True


def resolve_runtime_providers(db: Session) -> list[RuntimeProvider]:
    """返回按优先级排序的运行态 AI 提供商列表：[primary, fallback]。

    若数据库没有已启用的 primary/fallback 配置，返回空列表（由 ai.py 自动回退 .env）。
    """
    rows = (
        db.execute(
            select(AiProviderConfig)
            .where(
                AiProviderConfig.enabled.is_(True),
                AiProviderConfig.role.in_(("primary", "fallback")),
            )
            .order_by(AiProviderConfig.id.asc())
        )
        .scalars()
        .all()
    )
    ordered = sorted(rows, key=lambda r: 0 if r.role == "primary" else 1)
    resolved: list[RuntimeProvider] = []
    for r in ordered:
        key = decrypt_secret(r.api_key_encrypted)
        if not key and r.vendor == "ollama":
            key = "ollama"
        if not key:
            continue
        resolved.append(
            RuntimeProvider(
                provider_id=r.id,
                code=r.code,
                name=r.name,
                vendor=r.vendor,
                base_url=r.base_url.strip(),
                api_key=key,
                model_classify=r.model_classify.strip(),
                model_generate=r.model_generate.strip(),
                daily_token_budget=int(r.daily_token_budget or 0),
                role=r.role,
            )
        )
    return resolved


def get_provider_today_tokens(db: Session, provider: AiProviderConfig) -> int:
    """估算该提供商今日已消耗 Token（按其配置的分类/生成模型统计 UTC 今日 ai_run）。"""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    models = {m.strip() for m in (provider.model_classify, provider.model_generate) if (m or "").strip()}
    if not models:
        return 0
    total = db.execute(
        select(func.coalesce(func.sum(AiRun.tokens), 0)).where(
            AiRun.created_at >= start,
            AiRun.model.in_(models),
        )
    ).scalar_one()
    return int(total or 0)


def test_provider_connectivity(
    provider: AiProviderConfig,
) -> tuple[bool, int, str, str]:
    """连通性探测：优先 GET /models（0 Token），若端点不支持再降级 1-token chat 探测。"""
    unsafe = validate_provider_base_url(provider.vendor, provider.base_url)
    if unsafe is not None:
        return False, 0, "blocked", f"Base URL 安全校验失败：{unsafe}"

    api_key = decrypt_secret(provider.api_key_encrypted)
    if not api_key and provider.vendor != "ollama":
        return False, 0, "config", "未配置 API Key，请先编辑填写后再测试"

    base = provider.base_url.rstrip("/")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=12.0, follow_redirects=False) as client:
            resp = client.get(f"{base}/models", headers=headers)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 200:
                return (
                    True,
                    latency_ms,
                    "GET /models",
                    "接口鉴权与连通正常（0 Token 消耗）",
                )
            if resp.status_code in (401, 403):
                return (
                    False,
                    latency_ms,
                    "GET /models",
                    f"鉴权失败（HTTP {resp.status_code}），请检查 API Key 是否有效",
                )
            if resp.status_code not in (404, 405):
                snippet = resp.text[:160].replace("\n", " ")
                return (
                    False,
                    latency_ms,
                    "GET /models",
                    f"上游返回 HTTP {resp.status_code}：{snippet}",
                )

            # 部分兼容网关不实现 /models，降级做极短 1-token chat 探测
            chat_resp = client.post(
                f"{base}/chat/completions",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": provider.model_classify,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            if chat_resp.status_code == 200:
                return (
                    True,
                    latency_ms,
                    "POST /chat/completions",
                    f"模型 {provider.model_classify} 响应正常",
                )
            snippet = chat_resp.text[:160].replace("\n", " ")
            return (
                False,
                latency_ms,
                "POST /chat/completions",
                f"对话接口返回 HTTP {chat_resp.status_code}：{snippet}",
            )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return False, latency_ms, "network", f"连接失败：{type(exc).__name__}: {exc}"
