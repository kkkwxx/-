"""AI 调用封装（硬约束见 AI编码对照 §2.5；支持分类 + 文案生成 + 主备自动降级）。

核心保障：
1. 强制 ``response_format={"type": "json_object"}`` + 围栏剥离兜底 + 1 次自动重试；
2. 进程级按 ``(OpenAI, api_key, base_url)`` 复用的客户端缓存（同时感知单测 monkeypatch）；
3. 支持数据库 ``ai_provider_config`` 多提供商「主用 + 备用」自动降级（主用超日预算或报 429/5xx/连接错误自动切备用），无数据库配置时自动回退 ``.env`` 中的 ``DASHSCOPE_API_KEY``；
4. 统一接入 ``REPLAY_MODE`` 回放 → ``ai_run`` 历史成功缓存复用（0 Token）→ 日预算熔断；
5. 永不向外抛异常：失败返回 ``status="failed"``，超预算/无 Key 返回 ``status="skipped"``。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, settings
from app.models.ai import AiRun

logger = logging.getLogger(__name__)

# 回放目录：与 fixtures/ 同级约定，只读
REPLAY_DIR = REPO_ROOT / "fixtures" / "replay" / "ai"

# 百炼在部分模型下仍会返回 json 围栏（A-03），这里做最后一道兜底
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)

# 单次调用超时（秒）
_REQUEST_TIMEOUT = 45.0

SYSTEM_PROMPT = (
    "你是一个严谨的新闻事件判定器。"
    "用户消息是一个 JSON 对象，其中 instructions 字段描述了判定任务与输出字段。"
    "只输出一个 JSON object，不要输出任何解释、不要使用 markdown 代码围栏。"
)

GENERATE_SYSTEM_PROMPT = (
    "你是娱乐 App「神秘新闻」栏目的资深编辑。核心原则：标题负责勾人，正文负责说清楚；"
    "标题可以悬，正文不能悬。所有事实必须严格锚定在用户提供的原文中，严禁编造或夸大结论。"
    "只输出一个严格符合要求的 JSON object，不要输出任何解释或 markdown 代码围栏。"
)

# 进程级客户端缓存（按构造器 + key + base_url 分桶，感知单测 monkeypatch）
_client_lock = threading.Lock()
_cached_clients: dict[tuple[Any, str, str], Any] = {}


def get_openai_client(
    *, api_key: str | None = None, base_url: str | None = None
) -> Any:
    """获取进程级复用的 OpenAI 兼容客户端。"""
    key = settings.DASHSCOPE_API_KEY if api_key is None else api_key
    url = settings.DASHSCOPE_BASE_URL if base_url is None else base_url
    sig = (OpenAI, key, url)
    client = _cached_clients.get(sig)
    if client is not None:
        return client
    with _client_lock:
        client = _cached_clients.get(sig)
        if client is None:
            client = OpenAI(api_key=key, base_url=url)
            _cached_clients[sig] = client
    return client


def reset_client() -> None:
    """测试用：清空单例缓存。"""
    with _client_lock:
        _cached_clients.clear()


@dataclass
class AiResult:
    """一次 AI 调用的结果。``data={}`` 表示没拿到可用 JSON。"""

    data: dict = field(default_factory=dict)
    tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error: str | None = None
    cached: bool = False
    model_used: str = ""
    provider_code: str = ""


def input_hash(payload: dict, *, model: str, prompt_version: str) -> str:
    """``ai_run.input_hash``：``sha256(model + prompt_version + 规范化 payload JSON)``。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(
        f"{model}\x00{prompt_version}\x00{canonical}".encode("utf-8", errors="replace")
    ).hexdigest()


def _parse_json(raw: str) -> dict:
    """剥围栏 → json.loads → 必须是 dict。"""
    match = _FENCE_RE.search(raw or "")
    text = match.group(1) if match else (raw or "")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"模型返回不是 JSON object：{type(data).__name__}")
    return data


def _read_replay(hash_value: str, *, replay_key: str | None = None) -> AiResult:
    """读取离线回放 JSON。优先匹配 ``{input_hash}.json``，其次匹配显式 ``{replay_key}.json``。"""
    candidates = [REPLAY_DIR / f"{hash_value}.json"]
    if replay_key:
        safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", replay_key)
        candidates.append(REPLAY_DIR / f"{safe}.json")

    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return AiResult(
            data={}, status="skipped", error=f"回放文件缺失：{candidates[0].name}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return AiResult(data={}, status="failed", error=f"回放文件损坏：{exc}")
    if not isinstance(payload, dict):
        return AiResult(data={}, status="failed", error="回放文件不是 JSON object")
    return AiResult(data=payload, status="success", cached=True)


def get_today_tokens(
    session: Session, *, models: set[str] | None = None
) -> int:
    """统计今日（UTC 零点起）已消耗 Token 总量；可选按模型集合过滤。"""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(AiRun.tokens), 0)).where(AiRun.created_at >= start)
    if models:
        stmt = stmt.where(AiRun.model.in_(models))
    total = session.execute(stmt).scalar_one()
    return int(total or 0)


def lookup_cached_result(session: Session, hash_value: str) -> dict | None:
    """查 ``ai_run`` 历史成功缓存，命中直接复用（0 Token）。"""
    row = session.execute(
        select(AiRun.output_json)
        .where(
            AiRun.input_hash == hash_value,
            AiRun.status == "success",
            AiRun.output_json.is_not(None),
        )
        .order_by(AiRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return dict(row) if isinstance(row, dict) else None


class _JsonParseWithUsageError(ValueError):
    """JSON 解析失败但 API 已实际扣费，携带已消耗 total_tokens 防止漏记账。"""

    def __init__(self, message: str, tokens: int) -> None:
        super().__init__(message)
        self.tokens = tokens


def _call_once(
    client: Any,
    model: str,
    payload: dict,
    *,
    system_prompt: str,
    temperature: float = 0.0,
    is_dashscope: bool = True,
) -> tuple[dict, int]:
    """一次真实调用。返回值：``(data, tokens)``；JSON 解析失败也保留已耗 tokens。"""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "timeout": _REQUEST_TIMEOUT,
    }
    # 仅阿里百炼兼容接口附加 enable_thinking=False，避免 OpenAI/DeepSeek/Gemini 报未知参数
    if is_dashscope:
        kwargs["extra_body"] = {"enable_thinking": False}

    completion = client.chat.completions.create(**kwargs)
    raw = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    tokens = int(getattr(usage, "total_tokens", 0) or 0)
    try:
        parsed = _parse_json(raw)
    except Exception as exc:
        raise _JsonParseWithUsageError(f"{type(exc).__name__}: {exc}", tokens) from exc
    return parsed, tokens


@dataclass(frozen=True)
class _AttemptTarget:
    provider_code: str
    vendor: str
    base_url: str
    api_key: str
    model: str
    daily_budget: int
    budget_models: set[str] | None


def _build_attempt_targets(
    *,
    session: Session | None,
    default_model: str,
    stage: str | None,
) -> list[_AttemptTarget]:
    """构建按优先级排列的调用目标（优先 DB 主备提供商，否则回退 .env 百炼配置）。"""
    if session is not None:
        try:
            from app.services.ai_providers import resolve_runtime_providers

            db_providers = resolve_runtime_providers(session)
        except Exception:
            db_providers = []
        if db_providers:
            targets: list[_AttemptTarget] = []
            for p in db_providers:
                eff_model = (
                    p.model_classify
                    if stage == "classify"
                    else p.model_generate
                    if stage == "generate"
                    else default_model
                )
                model_set = {m for m in (p.model_classify, p.model_generate) if m}
                targets.append(
                    _AttemptTarget(
                        provider_code=p.code,
                        vendor=p.vendor,
                        base_url=p.base_url,
                        api_key=p.api_key,
                        model=eff_model or default_model,
                        daily_budget=p.daily_token_budget,
                        budget_models=model_set or None,
                    )
                )
            return targets

    # 回退到 .env 中的默认百炼配置
    if not settings.DASHSCOPE_API_KEY:
        return []
    return [
        _AttemptTarget(
            provider_code="env_dashscope",
            vendor="dashscope",
            base_url=settings.DASHSCOPE_BASE_URL,
            api_key=settings.DASHSCOPE_API_KEY,
            model=default_model,
            daily_budget=int(settings.LLM_DAILY_TOKEN_BUDGET or 0),
            budget_models=None,
        )
    ]


def call_structured(
    payload: dict,
    *,
    model: str,
    system_prompt: str,
    prompt_version: str,
    temperature: float = 0.0,
    session: Session | None = None,
    replay_key: str | None = None,
    stage: str | None = None,
) -> AiResult:
    """统一结构化 JSON 调用入口（支持主备提供商自动降级）。**永不抛异常**。"""
    base_hash = input_hash(payload, model=model, prompt_version=prompt_version)

    if settings.REPLAY_MODE:
        return _read_replay(base_hash, replay_key=replay_key)

    if session is not None:
        cached = lookup_cached_result(session, base_hash)
        if cached is not None:
            return AiResult(
                data=cached,
                tokens=0,
                latency_ms=0,
                status="success",
                cached=True,
                model_used=model,
            )

    targets = _build_attempt_targets(
        session=session, default_model=model, stage=stage
    )
    if not targets:
        return AiResult(data={}, status="skipped", error="DASHSCOPE_API_KEY 未配置")

    started = time.perf_counter()
    total_tokens = 0
    last_error: str | None = None
    all_skipped_by_budget = True
    last_budget_msg: str | None = None

    for idx, target in enumerate(targets):
        target_hash = input_hash(
            payload, model=target.model, prompt_version=prompt_version
        )
        if session is not None:
            if target_hash != base_hash:
                cached = lookup_cached_result(session, target_hash)
                if cached is not None:
                    return AiResult(
                        data=cached,
                        tokens=0,
                        latency_ms=0,
                        status="success",
                        cached=True,
                        model_used=target.model,
                        provider_code=target.provider_code,
                    )
            if target.daily_budget > 0:
                used = get_today_tokens(session, models=target.budget_models)
                if used >= target.daily_budget:
                    last_budget_msg = f"已达今日 Token 预算上限（{target.daily_budget}），已熔断跳过"
                    logger.warning(
                        "AI 提供商 %s 已达今日预算（%s/%s），尝试降级下一个提供商",
                        target.provider_code,
                        used,
                        target.daily_budget,
                    )
                    continue

        all_skipped_by_budget = False
        client = get_openai_client(api_key=target.api_key, base_url=target.base_url)
        is_dash = target.vendor == "dashscope" or "dashscope.aliyuncs.com" in target.base_url

        # 1 次正常 + 1 次重试（§2.5 第 2 条）；若存在备用提供商且主用失败则自动降级切换
        for attempt in (1, 2):
            try:
                data, tokens = _call_once(
                    client,
                    target.model,
                    payload,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    is_dashscope=is_dash,
                )
                total_tokens += tokens
                return AiResult(
                    data=data,
                    tokens=total_tokens,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    status="success",
                    model_used=target.model,
                    provider_code=target.provider_code,
                )
            except _JsonParseWithUsageError as exc:
                total_tokens += exc.tokens
                last_error = str(exc)
                logger.warning(
                    "AI 返回非法 JSON（已记账 tokens=%s）provider=%s attempt=%s model=%s hash=%s error=%s",
                    exc.tokens,
                    target.provider_code,
                    attempt,
                    target.model,
                    target_hash[:12],
                    last_error,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "AI 调用失败 provider=%s attempt=%s model=%s hash=%s error=%s",
                    target.provider_code,
                    attempt,
                    target.model,
                    target_hash[:12],
                    last_error,
                )

        if idx + 1 < len(targets):
            logger.warning(
                "主用 AI 提供商 %s 失败，自动降级至备用提供商 %s",
                target.provider_code,
                targets[idx + 1].provider_code,
            )

    if all_skipped_by_budget and last_budget_msg:
        return AiResult(data={}, status="skipped", error=last_budget_msg)

    return AiResult(
        data={},
        tokens=total_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
        status="failed",
        error=(last_error or "")[:2000],
    )


def classify(
    payload: dict,
    *,
    prompt_version: str | None = None,
    session: Session | None = None,
    replay_key: str | None = None,
) -> AiResult:
    """事件判定与实体抽取结构化调用。"""
    return call_structured(
        payload,
        model=settings.LLM_MODEL_CLASSIFY,
        system_prompt=SYSTEM_PROMPT,
        prompt_version=prompt_version or settings.PROMPT_VERSION,
        temperature=0.0,
        session=session,
        replay_key=replay_key,
        stage="classify",
    )


def generate(
    payload: dict,
    *,
    model: str | None = None,
    prompt_version: str | None = None,
    session: Session | None = None,
    replay_key: str | None = None,
) -> AiResult:
    """事实锚点 + 悬念标题候选 + 导语破题 + 大白话摘要生成调用。"""
    return call_structured(
        payload,
        model=model or settings.LLM_MODEL_GENERATE,
        system_prompt=GENERATE_SYSTEM_PROMPT,
        prompt_version=prompt_version or settings.GENERATE_PROMPT_VERSION,
        temperature=0.4,
        session=session,
        replay_key=replay_key,
        stage="generate",
    )
