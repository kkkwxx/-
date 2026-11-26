"""AI 调用薄封装（硬约束见 AI编码对照 §2.5；用例见 05 §6.3）。

四条规矩，逐条对齐 §2.5：

1. 结构化调用**必须显式传** ``response_format={"type": "json_object"}``——
   默认文本模式会返回带 ```json 围栏的内容，``json.loads`` 直接崩（A-03）；
2. 仍保留**围栏剥离兜底**，再 ``json.loads``，非法则重试 1 次；
3. 再失败**不抛异常**：返回 ``status="failed"``，由调用方落 ``pending``（判不准不丢）；
4. ``REPLAY_MODE=True`` 时只读 ``fixtures/replay/ai/{input_hash}.json``，
   只替换外部 IO，不绕过任何判定闸门（04 §15）。

本模块只管「把 dict 发出去、把 dict 收回来」，**不做业务判定**——
判定表在 ``app/services/preprocess.py``。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from app.core.config import REPO_ROOT, settings

logger = logging.getLogger(__name__)

# 回放目录：与 fixtures/ 同级约定，只读
REPLAY_DIR = REPO_ROOT / "fixtures" / "replay" / "ai"

# 百炼在部分模型下仍会返回 json 围栏（A-03），这里做最后一道兜底
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)

# 单次调用超时（秒）。与采集超时无关：AI 是同步小请求，30 s 足够
_REQUEST_TIMEOUT = 30.0

# 分类阶段的 system prompt：只声明契约，具体判定要点由调用方放进 payload
SYSTEM_PROMPT = (
    "你是一个严谨的新闻事件判定器。"
    "用户消息是一个 JSON 对象，其中 instructions 字段描述了判定任务与输出字段。"
    "只输出一个 JSON object，不要输出任何解释、不要使用 markdown 代码围栏。"
)


@dataclass
class AiResult:
    """一次 AI 调用的结果。``data={}`` 表示没拿到可用 JSON。"""

    data: dict = field(default_factory=dict)
    tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error: str | None = None


def input_hash(payload: dict, *, model: str, prompt_version: str) -> str:
    """``ai_run.input_hash`` 的口径（05 §6.3）。

    ```sha256(model + prompt_version + <payload 的规范化 JSON>)```：
    比文档里 ``model + prompt_version + title + summary`` 多带了 publisher/source_level，
    好处是输入一变就换 hash，缓存不会串味。
    """
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


def _read_replay(hash_value: str) -> AiResult:
    path = REPLAY_DIR / f"{hash_value}.json"
    if not path.is_file():
        return AiResult(
            data={}, status="skipped", error=f"回放文件缺失：{path.name}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return AiResult(data={}, status="failed", error=f"回放文件损坏：{exc}")
    if not isinstance(payload, dict):
        return AiResult(data={}, status="failed", error="回放文件不是 JSON object")
    return AiResult(data=payload, status="success")


def _call_once(client: OpenAI, model: str, payload: dict) -> tuple[dict, int]:
    """一次真实调用。返回值：``(data, tokens)``；异常向上抛，由 classify 兜。"""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            # 输入是 dict，序列化成 user content（05 §6.3：只传 title/summary/元信息，不传正文）
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        # §2.5 第 1 条：必须显式传，否则返回围栏包裹的文本
        "response_format": {"type": "json_object"},
        "temperature": 0,
        # 百炼参数：关掉思考链，否则会先输出一大段推理
        "extra_body": {"enable_thinking": False},
        "timeout": _REQUEST_TIMEOUT,
    }
    completion = client.chat.completions.create(**kwargs)
    raw = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    tokens = int(getattr(usage, "total_tokens", 0) or 0)
    return _parse_json(raw), tokens


def classify(payload: dict, *, prompt_version: str | None = None) -> AiResult:
    """结构化判定调用。**永不抛异常**（§2.5 第 2 条）。"""
    version = prompt_version or settings.PROMPT_VERSION
    model = settings.LLM_MODEL_CLASSIFY
    hash_value = input_hash(payload, model=model, prompt_version=version)

    if settings.REPLAY_MODE:
        return _read_replay(hash_value)

    if not settings.DASHSCOPE_API_KEY:
        # 没有 key 不是错误，是「跳过」：调用方据此落 pending，不丢数据
        return AiResult(data={}, status="skipped", error="DASHSCOPE_API_KEY 未配置")

    client = OpenAI(
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=settings.DASHSCOPE_BASE_URL,
    )

    started = time.perf_counter()
    last_error: str | None = None
    tokens = 0

    # 1 次正常 + 1 次重试（§2.5 第 2 条）
    for attempt in (1, 2):
        try:
            data, tokens = _call_once(client, model, payload)
            return AiResult(
                data=data,
                tokens=tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="success",
            )
        except Exception as exc:  # 网络 / JSON / 非 dict 一律重试
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "AI 分类调用失败 attempt=%s model=%s hash=%s error=%s",
                attempt,
                model,
                hash_value[:12],
                last_error,
            )

    return AiResult(
        data={},
        tokens=tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
        status="failed",
        error=(last_error or "")[:2000],
    )
