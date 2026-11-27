"""07 AI 内容加工引擎：事实锚点 → 3—5 标题候选 → 导语破题 → 大白话摘要 → 轻校验 → 审核队列路由。

核心原则（需求文档 v1.0 §1 / §7 / §8）：
- 标题负责勾人，正文负责说清楚；标题可以悬，正文不能悬；
- 事实锚点先于标题生成，所有表述严格锚定原文；
- 轻校验不通过时带报错原因自动重生成 1 次；仍不通过则落入 ``pending`` 人工复核台；
- C 级源、灵异/异常类、低置信度或带风险标记的内容一律落 ``pending``。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.ai import AiRun
from app.models.pipeline import Article, RawItem
from app.models.task import TaskRun
from app.services import ai
from app.services.policy import get_active_policy
from app.services.verifier import (
    SUPERNATURAL_NATURES,
    verify_content,
    verify_title,
)

logger = logging.getLogger(__name__)

BODY_EXCERPT_LIMIT = 1500

_GEN_INSTRUCTIONS = """请为下面这条真实事件新闻撰写「神秘新闻」栏目内容，严格遵守以下规则：

1. 先提炼【fact_anchor（事实锚点）】：
   用 1—2 句纯事实写清：谁/什么机构 + 做了什么/发生什么 + 关键时间或数字 + 限定条件（如“候选”“无撞击威胁”“实验室阶段”“娱乐节目”）。绝不允许编造原文没有的数字或结论。如果 has_full_text=false，严禁脑补摘要之外的细节。

2. 再生成【title_candidates（3—5 个标题候选）】：
   - 风格：娱乐 App 短视频封面文案，轻松、悬疑、抓眼球；只保留钩子前半句，去标点 10—18 字（至少 7 字、不超过 18 字）；
   - 允许问号、引号、感叹号、电影梗、游戏梗，可适度使用：鬼、幽灵、外星人、地球2.0、贴脸、直扑、擦肩、杀进、炸场、封神、翻车、降维打击、活久见、细思极恐、现形、逮到、有戏、科幻成真、不是科幻；
   - 严禁在标题后加解释句（禁止“原来是……”“别怕，这是……”）；
   - 严禁底线词：已确认外星人、地球将毁灭、政府隐瞒真相、人类已掌握；
   - 每条给 hook_score（1—5 整数）和 style_tag（恐怖片式/科幻片式/反转式/贴脸式/活久见式/吃瓜式）。
   示例风格：科学家抓到“鬼”了？ / 地球2.0找到了？ / 小行星贴脸地球！ / 不用手也能驭剑？ / 中国道士杀进泰国灵异综艺！

3. 撰写【body_lead（正文第一段，20—60 字）】：
   - 针对最高分标题的悬念立刻“破题解密”：点出事实主体、发生了什么、关键时间/数字与限定词；
   - 若为灵异/异常/综艺/UFO 类，必须明确注明“娱乐节目，别当真”或“未经科学证实/网络讨论”。

4. 撰写【body_summary（正文大白话摘要，80—120 字）】：
   - 说清“谁、做了什么、结果如何、为什么值得看”；
   - 专业术语首次出现加括号大白话解释（如“中微子（几乎不与物质反应的幽灵粒子）”）；
   - 禁止使用“据悉”“日前”“相关研究显示”等公文套话。

只输出一个严格 JSON object，格式如下：
{
  "fact_anchor": "中国江门中微子实验（JUNO）发布首个物理成果，测得中微子关键参数。",
  "title_candidates": [
    {"title": "科学家抓到“鬼”了？", "style_tag": "恐怖片式", "hook_score": 5, "rationale": "用幽灵粒子双关制造悬念"},
    {"title": "地下700米逮到幽灵粒子？", "style_tag": "科幻片式", "hook_score": 4, "rationale": "突出实验场景"},
    {"title": "中国大装置拍到宇宙隐身客！", "style_tag": "活久见式", "hook_score": 4, "rationale": "口语化拟人"}
  ],
  "body_lead": "别怕，这里的“鬼”是几乎不跟物质反应的中微子，也叫“幽灵粒子”。中国江门中微子实验这次测到了关键数据。",
  "body_summary": "位于广东地下700米的江门中微子实验（专门捕捉幽灵粒子的巨型球形探测器）团队宣布发布首个物理成果，成功测定中微子振荡的关键精度数据。这不仅验证了装置世界领先的灵敏度，也为人类破解宇宙物质起源之谜迈出关键一步。",
  "self_risk_flags": []
}"""


class TitleCandidateItem(BaseModel):
    title: str
    style_tag: str = "反转式"
    hook_score: int = Field(default=3, ge=1, le=5)
    rationale: str = ""


class AiGenerationSchema(BaseModel):
    fact_anchor: str = ""
    title_candidates: list[TitleCandidateItem] = Field(default_factory=list)
    body_lead: str = ""
    body_summary: str = ""
    self_risk_flags: list[str] = Field(default_factory=list)


@dataclass
class GenerateResult:
    fact_anchor: str | None = None
    title_candidates: list[dict] = field(default_factory=list)
    title_final: str | None = None
    body_lead: str | None = None
    body_summary: str | None = None
    hook_score: int | None = None
    style_tag: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    status: str = "pending"  # 终态：candidate 或 pending
    ai_status: str = "success"
    ai_error: str | None = None
    tokens: int = 0
    latency_ms: int = 0
    input_hash: str = ""
    output: dict = field(default_factory=dict)


def _build_gen_payload(
    *,
    title: str,
    summary: str | None,
    full_text: str | None,
    has_full_text: bool,
    publisher_name: str | None,
    source_level: str,
    event_type: str | None,
    content_nature: str | None,
    event_time: datetime | None,
    retry_feedback: list[str] | None = None,
) -> dict:
    payload: dict = {
        "instructions": _GEN_INSTRUCTIONS,
        "title": title,
        "summary": (summary or "")[:800],
        "body_excerpt": (full_text or "")[:BODY_EXCERPT_LIMIT] if has_full_text else "",
        "has_full_text": has_full_text,
        "publisher_name": publisher_name or "",
        "source_level": source_level,
        "event_type": event_type or "deep_space_particle",
        "content_nature": content_nature or "科研进展",
        "event_time": event_time.strftime("%Y-%m-%d %H:%M UTC") if event_time else None,
    }
    if retry_feedback:
        payload["retry_correction"] = (
            "上次生成未通过轻校验，请务必修正以下问题：" + "；".join(retry_feedback)
        )
    return payload


def _select_best_candidate(
    candidates: list[TitleCandidateItem],
    *,
    title_min_chars: int | None = None,
    title_max_chars: int | None = None,
) -> tuple[dict | None, list[dict]]:
    """逐条校验候选标题，按 passed → hook_score → style_word 选最优。"""
    enriched: list[dict] = []
    for item in candidates[:5]:
        check = verify_title(
            item.title,
            title_min_chars=title_min_chars,
            title_max_chars=title_max_chars,
        )
        enriched.append(
            {
                "title": item.title.strip(),
                "style_tag": (item.style_tag or "反转式").strip()[:32],
                "hook_score": max(1, min(5, int(item.hook_score))),
                "rationale": (item.rationale or "").strip()[:200],
                "passed_check": check.passed,
                "check_errors": check.errors,
                "effective_chars": check.effective_chars,
                "_has_style": check.has_style_word,
            }
        )

    if not enriched:
        return None, []

    ranked = sorted(
        enriched,
        key=lambda c: (
            1 if c["passed_check"] else 0,
            c["hook_score"],
            1 if c["_has_style"] else 0,
        ),
        reverse=True,
    )
    best = ranked[0]
    cleaned_list = [{k: v for k, v in c.items() if not k.startswith("_")} for c in enriched]
    best_clean = {k: v for k, v in best.items() if not k.startswith("_")}
    return best_clean, cleaned_list


def decide_review_bucket(
    *,
    source_level: str,
    event_type: str | None,
    content_nature: str | None,
    event_confidence: float | None,
    risk_flags: list[str],
    verify_passed: bool,
    event_confidence_min: float | None = None,
) -> str:
    """决定成品进入 ``candidate``（常规候选池）还是 ``pending``（待定复核池）。

    需求 §8 / §11：C 级源、灵异边界、高风险、低置信度或轻校验失败一律进 ``pending``。
    """
    if source_level == "C":
        return "pending"
    if event_type == "anomaly_psychic" or content_nature in SUPERNATURAL_NATURES:
        return "pending"
    if not verify_passed:
        return "pending"
    ev_min = float(event_confidence_min if event_confidence_min is not None else settings.EVENT_CONFIDENCE_MIN)
    if event_confidence is not None and float(event_confidence) < ev_min:
        return "pending"
    # 存在任何阻断或高风险标记（仅保留 cross_verified_by_ab 这类正向标记不阻断）
    blocking_flags = [
        f for f in risk_flags if f not in ("cross_verified_by_ab", "bureaucratic_cliche", "body_summary_length")
    ]
    if blocking_flags:
        return "pending"
    return "candidate"


def stage_generate(
    *,
    title: str,
    summary: str | None,
    full_text: str | None,
    has_full_text: bool,
    publisher_name: str | None,
    source_level: str,
    event_type: str | None,
    content_nature: str | None,
    event_confidence: float | None,
    event_time: datetime | None,
    existing_risk_flags: list[str] | None = None,
    replay_key: str | None = None,
    event_confidence_min: float | None = None,
    title_min_chars: int | None = None,
    title_max_chars: int | None = None,
    require_full_text_for_candidate: bool = False,
) -> GenerateResult:
    """执行一次 AI 文案加工 + 轻校验（不通过时自动重生成 1 次）。**不碰数据库**。"""
    base_flags = list(existing_risk_flags or [])
    if not has_full_text and "no_full_text" not in base_flags:
        base_flags.append("no_full_text")

    total_tokens = 0
    total_latency = 0
    last_hash = ""
    last_output: dict = {}
    retry_feedback: list[str] | None = None
    best_result: GenerateResult | None = None

    for attempt in (1, 2):
        payload = _build_gen_payload(
            title=title,
            summary=summary,
            full_text=full_text,
            has_full_text=has_full_text,
            publisher_name=publisher_name,
            source_level=source_level,
            event_type=event_type,
            content_nature=content_nature,
            event_time=event_time,
            retry_feedback=retry_feedback,
        )
        model = settings.LLM_MODEL_GENERATE
        last_hash = ai.input_hash(
            payload, model=model, prompt_version=settings.GENERATE_PROMPT_VERSION
        )
        r_key = replay_key if attempt == 1 else (f"{replay_key}_retry" if replay_key else None)
        res = ai.generate(
            payload,
            model=model,
            prompt_version=settings.GENERATE_PROMPT_VERSION,
            replay_key=r_key,
        )
        total_tokens += res.tokens
        total_latency += res.latency_ms

        if res.status != "success" or not res.data:
            return GenerateResult(
                risk_flags=list(dict.fromkeys([*base_flags, "ai_generate_failed"])),
                status="pending",
                ai_status=res.status,
                ai_error=res.error,
                tokens=total_tokens,
                latency_ms=total_latency,
                input_hash=last_hash,
                output={},
            )

        last_output = res.data
        try:
            parsed = AiGenerationSchema.model_validate(res.data)
        except ValidationError as exc:
            retry_feedback = [f"JSON 字段结构不合规：{exc.errors()[:1]}"]
            if attempt == 2:
                return GenerateResult(
                    risk_flags=list(dict.fromkeys([*base_flags, "ai_schema_invalid"])),
                    status="pending",
                    ai_status="failed",
                    ai_error=retry_feedback[0],
                    tokens=total_tokens,
                    latency_ms=total_latency,
                    input_hash=last_hash,
                    output=last_output,
                )
            continue

        best_cand, cand_list = _select_best_candidate(
            parsed.title_candidates,
            title_min_chars=title_min_chars,
            title_max_chars=title_max_chars,
        )
        title_final = best_cand["title"] if best_cand else None
        hook_score = best_cand["hook_score"] if best_cand else None
        style_tag = best_cand["style_tag"] if best_cand else None

        check = verify_content(
            title_final=title_final,
            body_lead=parsed.body_lead,
            body_summary=parsed.body_summary,
            fact_anchor=parsed.fact_anchor,
            event_type=event_type,
            content_nature=content_nature,
            source_level=source_level,
            title_min_chars=title_min_chars,
            title_max_chars=title_max_chars,
        )

        merged_flags = list(
            dict.fromkeys([*base_flags, *check.risk_flags, *(parsed.self_risk_flags or [])])
        )
        # 严格档位下无全文（no_full_text）也作为阻断项进入待定池；均衡/宽松档位允许摘要源进候选池
        routing_flags = (
            list(merged_flags)
            if require_full_text_for_candidate
            else [f for f in merged_flags if f != "no_full_text"]
        )
        bucket = decide_review_bucket(
            source_level=source_level,
            event_type=event_type,
            content_nature=content_nature,
            event_confidence=event_confidence,
            risk_flags=routing_flags,
            verify_passed=check.passed,
            event_confidence_min=event_confidence_min,
        )

        best_result = GenerateResult(
            fact_anchor=parsed.fact_anchor.strip() or None,
            title_candidates=cand_list,
            title_final=title_final,
            body_lead=parsed.body_lead.strip() or None,
            body_summary=parsed.body_summary.strip() or None,
            hook_score=hook_score,
            style_tag=style_tag,
            risk_flags=merged_flags,
            status=bucket,
            ai_status="success",
            ai_error="; ".join(check.errors) if check.errors else None,
            tokens=total_tokens,
            latency_ms=total_latency,
            input_hash=last_hash,
            output=last_output,
        )

        if check.passed:
            return best_result

        # 轻校验未通过 → 拼装纠错反馈再生成 1 次
        retry_feedback = check.errors

    assert best_result is not None
    if "verify_failed" not in best_result.risk_flags:
        best_result.risk_flags.append("verify_failed")
    best_result.status = "pending"
    return best_result


# --------------------------------------------------------------------------- #
# 批量执行与 Worker 集成
# --------------------------------------------------------------------------- #


def count_judged(session: Session) -> int:
    """统计等待 AI 文案加工的 ``status='judged'`` 文章数。"""
    return int(
        session.execute(
            select(func.count()).select_from(Article).where(Article.status == "judged")
        ).scalar_one()
        or 0
    )


def apply_generation_to_article(
    session: Session, article: Article, generated: GenerateResult
) -> None:
    """把 AI 加工结果写回 Article 并记录 ai_run。"""
    article.fact_anchor = generated.fact_anchor
    article.title_candidates = list(generated.title_candidates)
    article.title_final = generated.title_final
    article.body_lead = generated.body_lead
    article.body_summary = generated.body_summary
    article.hook_score = generated.hook_score
    article.style_tag = generated.style_tag
    article.risk_flags = list(generated.risk_flags)
    article.need_review = True
    article.status = generated.status
    article.ai_generated_at = datetime.now(timezone.utc)
    if generated.hook_score:
        article.review_priority = int(article.review_priority or 0) + generated.hook_score * 5

    session.add(
        AiRun(
            article_id=article.id,
            stage="generate",
            model=settings.LLM_MODEL_GENERATE,
            prompt_version=settings.GENERATE_PROMPT_VERSION,
            input_hash=generated.input_hash or "empty",
            output_json=generated.output or None,
            tokens=generated.tokens,
            latency_ms=generated.latency_ms,
            status=generated.ai_status,
            error=(generated.ai_error[:2000] if generated.ai_error else None),
        )
    )


def run_generate_batch(limit: int | None = None) -> dict:
    """Worker 第 5 个 Job：捞取 ``status='judged'`` 批量做 AI 加工 + 轻校验。"""
    batch = int(limit if limit is not None else settings.GENERATE_BATCH_SIZE)
    stats = {
        "scanned": 0,
        "candidate": 0,
        "pending": 0,
        "ai_failed": 0,
        "tokens": 0,
    }
    if batch <= 0:
        return stats

    session = SessionLocal()
    run_id: int | None = None
    try:
        run = TaskRun(
            task_type="generate_content",
            source_id=None,
            status="running",
            triggered_by="scheduler",
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        run_id = run.id
    except Exception:
        session.rollback()
        logger.exception("建 generate_content 任务失败")
        session.close()
        return stats

    try:
        pt = get_active_policy(session).thresholds
        rows = session.execute(
            select(Article, RawItem)
            .join(RawItem, Article.raw_item_id == RawItem.id)
            .where(Article.status == "judged")
            .order_by(Article.review_priority.desc(), Article.id.asc())
            .limit(batch)
        ).all()

        budget = int(settings.LLM_DAILY_TOKEN_BUDGET or 0)
        used = ai.get_today_tokens(session) if budget > 0 else 0

        jobs: list[tuple[Article, dict]] = []
        for article, raw in rows:
            stats["scanned"] += 1
            has_ft = raw.full_text_status in ("ok", "skipped") and bool(raw.full_text)
            jobs.append(
                (
                    article,
                    {
                        "title": article.title,
                        "summary": raw.raw_summary or article.summary,
                        "full_text": raw.full_text if has_ft else None,
                        "has_full_text": has_ft,
                        "publisher_name": raw.publisher_name or article.publisher_name,
                        "source_level": article.source_level,
                        "event_type": article.event_type,
                        "content_nature": article.content_nature,
                        "event_confidence": (
                            float(article.event_confidence)
                            if article.event_confidence is not None
                            else None
                        ),
                        "event_time": article.event_time,
                        "existing_risk_flags": list(article.risk_flags or []),
                        "event_confidence_min": pt.event_confidence_min,
                        "title_min_chars": pt.title_min_chars,
                        "title_max_chars": pt.title_max_chars,
                        "require_full_text_for_candidate": pt.require_full_text_for_candidate,
                    },
                )
            )

        if jobs:
            results_by_id: dict[int, GenerateResult] = {}
            est_per_gen = 2500
            reserved = 0
            to_run: list[tuple[Article, dict]] = []
            for article, kwargs in jobs:
                if budget > 0 and (used + reserved) >= budget:
                    results_by_id[article.id] = GenerateResult(
                        risk_flags=list(
                            dict.fromkeys([*kwargs["existing_risk_flags"], "budget_exceeded"])
                        ),
                        status="pending",
                        ai_status="skipped",
                        ai_error=f"已达今日 Token 预算上限（{budget}）",
                    )
                else:
                    reserved += est_per_gen
                    to_run.append((article, kwargs))

            if to_run:
                workers = max(1, int(settings.GENERATE_CONCURRENCY))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        article.id: pool.submit(stage_generate, **kwargs)
                        for article, kwargs in to_run
                    }
                    for article, _kwargs in to_run:
                        try:
                            results_by_id[article.id] = futures[article.id].result()
                        except Exception:
                            logger.exception("AI 文案生成线程异常 article_id=%s", article.id)

            for article, _kwargs in jobs:
                generated = results_by_id.get(article.id)
                if generated is None:
                    continue
                try:
                    apply_generation_to_article(session, article, generated)
                    stats["tokens"] += generated.tokens
                    if generated.ai_status != "success":
                        stats["ai_failed"] += 1
                    if generated.status == "candidate":
                        stats["candidate"] += 1
                    else:
                        stats["pending"] += 1
                    session.commit()
                except Exception:
                    session.rollback()
                    logger.exception("AI 文案回写失败 article_id=%s", article.id)
    except Exception:
        session.rollback()
        logger.exception("generate_content 批次异常 task_run=%s", run_id)
    finally:
        session.close()

    _close_generate_run(run_id, stats)
    return stats


def _close_generate_run(run_id: int | None, stats: dict) -> None:
    if run_id is None:
        return
    session = SessionLocal()
    try:
        run = session.get(TaskRun, run_id)
        if run is None:
            return
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.stats_json = dict(stats)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("收口 generate_content 任务失败 task_run_id=%s", run_id)
    finally:
        session.close()
