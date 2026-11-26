"""预处理与事件判定（口径：05-预处理与事件判定 全篇）。

链路（05 §2，顺序不能换）::

    L1 清洗  clean_title()
    L2 硬排除 stage_filter() → status='rule_rejected', reject_reason='rule:<category>:<word>'
    L3 软扣分 stage_filter() → priority -N（白名单可救回）
    L4 is_event + L5 五类 stage_judge()（AI，失败落 pending）
    L6 排序   review_priority（05 §8 公式）

三个设计约束：

1. **filter 与 judge 是两个独立函数**：06 的即时去重要插在两者之间（05 §2 / §9），
   所以 ``stage_filter`` 是纯函数（不做 IO、不查库），``stage_judge`` 只碰 AI 不碰库；
2. **不做有损删除**：被排除的条目只改 ``status='rule_rejected'`` 并记 ``reject_reason``；
3. **判不准不丢**：任何 AI 失败 / 低置信都落 ``pending``，绝不默认拒。
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, ValidationError
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.ai import AiRun
from app.models.keyword import KeywordRule
from app.models.pipeline import Article, RawItem
from app.models.task import TaskRun
from app.services import ai

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 常量（05 §6.1 / §6.4 / §7.1）
# --------------------------------------------------------------------------- #

EVENT_TYPES: tuple[str, ...] = (
    "deep_space_particle",
    "neo",
    "exoplanet",
    "bci_consciousness",
    "anomaly_psychic",
    # 01-总体架构的栏目图是「五类 + other / confidence」，other 是明确的兜底枚举
    "other",
)

# 05 §6.4：六种 content_nature，后四种属「灵异四类」→ 强制 pending
CONTENT_NATURES: tuple[str, ...] = (
    "科学事实",
    "科研进展",
    "未证实目击",
    "灵异传闻",
    "娱乐化灵异",
    "伪科学主张",
)
SUPERNATURAL_NATURES: frozenset[str] = frozenset(
    {"未证实目击", "灵异传闻", "娱乐化灵异", "伪科学主张"}
)

# 05 §6.1：六种 not_event 子类，直接写进 reject_reason
NOT_EVENT_CATEGORIES: tuple[str, ...] = (
    "explainer",
    "listicle",
    "industry",
    "opinion",
    "promo",
    "image",
)

# 05 §8：等级分 A+30 / B+15 / C 0
LEVEL_SCORE = {"A": 30, "B": 15, "C": 0}
ANCHOR_STEP = 5
ANCHOR_CAP = 10
SIGNAL_CAP = 6
SUPERNATURAL_PENALTY = 10

# 05 §6.3：只传 title + summary(截断 800 字) + publisher_name + source_level，**不传正文**
SUMMARY_LIMIT = 800

# 05 §4.4 第 1/2 条：去尾部媒体名。实采 232 条里出现过的站点尾巴，全部小写比较
MEDIA_SUFFIXES: frozenset[str] = frozenset(
    name.lower()
    for name in (
        # —— 任务书点名要收的 ——
        "Sohu", "36Kr", "Jiemian.com", "网易新闻客户端", "科学网—新闻", "财联社电报",
        "东方财富网", "新华网", "TradingView", "cnBeta.COM", "凤凰网", "虎嗅网",
        "第一财经", "上观新闻", "Mezha.net", "UA.NEWS", "禁闻网", "大纪元",
        "星島頭條", "早晨报", "光华日报", "文学城", "阿波罗新闻网", "新唐人電視台",
        "世界互联网大会", "国际科技创新中心", "上海浦东", "湖北日报新闻客户端",
        "湖北日报传媒集团", "投资界", "电商派", "驱动之家", "中华网财经",
        "信息化观察网", "健康报", "北京", "天津大学新闻网", "杭州网", "中金在线",
        "资鲸", "新榜", "品玩", "ByDrug", "AIBase", "越南网", "香港01", "TechNews",
        # —— 05-标题样本 里实采到的补充 ——
        "手机新浪网", "新浪新闻", "新浪新闻_手机新浪网", "新浪财经", "生物通", "21财经",
        "观点网", "东方财富", "同花顺", "同花顺财经", "大紀元", "Informat.ro",
        "新华网江苏频道", "新华网浙江", "新华网上海", "汽车之家", "新湖南",
        "Vietnam.vn", "게임메카", "18183游戏网", "17173", "yeeyi", "光明网",
        "北京市投资促进服务中心", "frostchina.com", "blockchain.news", "www.itsdw.cn",
        "itsdw.cn", "界面新闻", "澎湃新闻", "观察者网", "科学网", "财联社", "网易新闻",
        "网易", "搜狐", "36氪", "虎嗅", "品玩网", "智东西", "机器之心", "量子位",
    )
)

# 尾部噪声后缀（05 §4.4 第 3 条）：`_今日要闻` 之类
_NOISE_TAIL_RE = re.compile(r"[_\-–—|｜]\s*(?:今日要闻|要闻|快讯|资讯|滚动)\s*$", re.I)
_NOISE_INLINE_RE = re.compile(r"[【\[](?:无限服|更新|图集|视频|直播)[】\]]")
_TAG_RE = re.compile(r"<[^>]{1,200}>")
_WS_RE = re.compile(r"\s+")

# 尾部媒体名：分隔符 + 白名单名（长名优先，避免 "Sohu" 抢先吃掉 "Sohu.com"）
_MEDIA_TAIL_RE = re.compile(
    r"\s*[-–—|｜_·•]\s*("
    + "|".join(re.escape(name) for name in sorted(MEDIA_SUFFIXES, key=len, reverse=True))
    + r")\s*$",
    re.I,
)
# 尾部通用候选：交给「与 publisher_name 的相似度」判定（05 §4.4 第 2 条）
_TAIL_CANDIDATE_RE = re.compile(
    r"^(?P<body>.+?)\s*[-–—|｜]\s*(?P<tail>[^\-–—|｜]{1,40})$", re.S
)


# --------------------------------------------------------------------------- #
# 规则集
# --------------------------------------------------------------------------- #


@dataclass
class RuleSet:
    """从 ``keyword_rule`` 编译出的快照（05 §3）。"""

    hard: list[tuple[str, str, re.Pattern]] = field(default_factory=list)
    soft: list[tuple[str, int, re.Pattern]] = field(default_factory=list)
    anchors: list[tuple[str, re.Pattern]] = field(default_factory=list)
    verbs: re.Pattern | None = None
    signals: list[tuple[int, re.Pattern]] = field(default_factory=list)


def load_rules(session: Session) -> RuleSet:
    """只取 ``enabled=true``；编译失败记 warning 并跳过该条（一条坏正则不能拖垮整批）。"""
    rules = RuleSet()
    verb_patterns: list[str] = []
    rows = session.scalars(
        select(KeywordRule).where(KeywordRule.enabled.is_(True)).order_by(KeywordRule.id)
    ).all()

    for row in rows:
        try:
            compiled = re.compile(row.pattern, re.I)
        except re.error as exc:
            logger.warning(
                "keyword_rule 正则非法，已跳过 id=%s type=%s category=%s error=%s",
                row.id,
                row.type,
                row.category,
                exc,
            )
            continue
        if row.type == "exclude_hard":
            rules.hard.append((row.category, row.pattern, compiled))
        elif row.type == "exclude_soft":
            rules.soft.append((row.category, int(row.weight or 0), compiled))
        elif row.type == "anchor":
            rules.anchors.append((row.category, compiled))
        elif row.type == "signal":
            rules.signals.append((int(row.weight or 0), compiled))
        elif row.type == "event_verb":
            verb_patterns.append(row.pattern)

    if verb_patterns:
        try:
            rules.verbs = re.compile(
                "|".join(f"(?:{pattern})" for pattern in verb_patterns), re.I
            )
        except re.error as exc:  # 理论上不会到（上面每条已单独编译过）
            logger.warning("event_verb 合并编译失败，白名单救回将失效：%s", exc)

    return rules


# --------------------------------------------------------------------------- #
# L1 清洗
# --------------------------------------------------------------------------- #


def clean_title(title: str, publisher_name: str | None) -> str:
    """标题清洗（05 §4.4）。

    **关键**：去掉的尾巴必须是「分隔符 + 媒体名」，媒体名要么在 ``MEDIA_SUFFIXES`` 里，
    要么与 ``publisher_name`` 高度相似（``fuzz.ratio >= 80``）。
    绝不能按 ``-`` 简单截断——反例：``完美世界(002624.SZ)：上半年净亏损1.18亿元 - TradingView``。
    """
    text = html.unescape(title or "").strip()
    if not text:
        return ""

    publisher = (publisher_name or "").strip()

    # 第 1、2、3 条：最多剥 3 层尾巴（媒体名可能叠着站点噪声），每轮只剥一层
    for _ in range(3):
        stripped = _strip_one_tail(text, publisher)
        if stripped == text:
            break
        text = stripped

    # 第 4 条：HTML 实体与标签兜底（04 §8.2 已做，这里防漏）
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)

    # 第 5 条：NFKC + 空白归一
    text = unicodedata.normalize("NFKC", text)
    text = _NOISE_INLINE_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    return text.strip(" -–—|｜_·•\u3000")


def _strip_one_tail(text: str, publisher: str) -> str:
    """剥掉一层尾部噪声/媒体名；没有可剥的就原样返回。"""
    # 第 3 条：`_今日要闻` 这类纯噪声，先剥（不需要相似度校验）
    without_noise = _NOISE_TAIL_RE.sub("", text)
    if without_noise != text:
        return without_noise.strip()

    # 第 1、2 条：分隔符 + 白名单媒体名
    match = _MEDIA_TAIL_RE.search(text)
    if match is not None and match.start() > 0:
        return text[: match.start()].rstrip()

    # 第 2 条：尾巴与 publisher_name 高度相似（GN 的 publisher_name 已单独存了）
    if publisher:
        candidate = _TAIL_CANDIDATE_RE.match(text)
        if candidate is not None:
            tail = candidate.group("tail").strip()
            body = candidate.group("body").strip()
            if body and tail and fuzz.ratio(tail.lower(), publisher.lower()) >= 80:
                return body
    return text


# --------------------------------------------------------------------------- #
# L2 / L3 / L6 规则过滤（纯函数）
# --------------------------------------------------------------------------- #


@dataclass
class FilterResult:
    cleaned_title: str
    hard: tuple[str, str] | None
    soft: tuple[str, int] | None
    anchors: set[str]
    verbs: list[str]
    signals: list[str]
    whitelisted: bool
    priority: int


def stage_filter(
    title: str,
    summary: str | None,
    *,
    source_level: str,
    rules: RuleSet,
) -> FilterResult:
    """L1—L3 与排序分。**纯函数**：不查库、不做 IO，``rules`` 由外部传入。"""
    cleaned = clean_title(title, None)
    # scope 语义＝title_summary（05 §3：排除词一律扫标题 + 摘要，不扫正文）
    haystack = f"{cleaned} {summary}".strip() if summary else cleaned

    # 硬排除：第一个命中的 (category, matched_text)
    hard: tuple[str, str] | None = None
    for category, _pattern, compiled in rules.hard:
        hit = compiled.search(haystack)
        if hit is not None:
            hard = (category, hit.group(0))
            break

    # 软扣分：取 weight 最负的一条，不累加（避免一条标题被打成 -9，05 §4.3）
    soft: tuple[str, int] | None = None
    for category, weight, compiled in rules.soft:
        if compiled.search(haystack) is None:
            continue
        if soft is None or weight < soft[1]:
            soft = (category, weight)

    anchors = {category for category, compiled in rules.anchors if compiled.search(haystack)}
    verbs = [m.group(0) for m in rules.verbs.finditer(haystack)] if rules.verbs else []

    signals: list[str] = []
    signal_score = 0
    for weight, compiled in rules.signals:
        for match in compiled.finditer(haystack):
            signals.append(match.group(0))
            signal_score += weight if weight else 2

    # 白名单救回：anchor + event_verb → 忽略全部软扣分（硬排除不受影响，05 §4.3）
    whitelisted = bool(anchors) and bool(verbs)

    priority = (
        LEVEL_SCORE.get(source_level, 0)
        + min(len(anchors) * ANCHOR_STEP, ANCHOR_CAP)
        + min(signal_score, SIGNAL_CAP)
        # weight 在种子文件里是负数（-3 / -2 / -5），这里是「扣分」，所以取绝对值
        - (0 if whitelisted or soft is None else abs(soft[1]))
    )

    return FilterResult(
        cleaned_title=cleaned,
        hard=hard,
        soft=soft,
        anchors=anchors,
        verbs=verbs,
        signals=signals,
        whitelisted=whitelisted,
        priority=priority,
    )


# --------------------------------------------------------------------------- #
# L4 / L5 AI 判定
# --------------------------------------------------------------------------- #

_INSTRUCTIONS = """你是新闻事件判定器。请阅读输入里的 title（标题）、summary（摘要）、
publisher_name（媒体名）、source_level（来源等级 A/B/C），按下面的口径判定。

【事件的定义】
事件 = 主体 + 动作 + 时间/地点线索 + 可核验。四缺一就不是事件，或只能算判不准。
是事件：《Webb 首次在系外行星大气中探测到水汽》《浙大团队用脑机接口治疗难治性抑郁症取得突破》
       《NASA 的 DART 撞击使小行星轨道改变》《患者植入脑机接口后用意念玩 GTA6》
不是事件：《暗物质是什么？一文读懂》（科普）《2026 脑机接口大会举办》（行业动态）
       《十大最神秘 UFO 事件盘点》（榜单）《政策加码+资本涌入 赋能脑机接口产业》（评论）

【不是事件时，category 必须取以下六值之一】
explainer=科普解读 / listicle=榜单盘点 / industry=行业动态会议园区政策 /
opinion=评论观点 / promo=活动预告招生招聘 / image=纯图片说明（如 APOD 每日一图）

【是事件时，event_type 必须取以下六值之一，归不进去用 other】
deep_space_particle=深空/粒子（天体物理、粒子物理、引力波、暗物质；主语是探测器/望远镜）
neo=近地天体（小行星/彗星自身的发现、飞掠、撞击、采样返回；主语是小行星）
exoplanet=新星球/类地行星（系外行星的发现与大气/宜居性观测）
bci_consciousness=脑机/意识（脑机接口、神经解码、意识研究；要有患者/受试者/实验/观测这类结果主体，
                 只有机构、园区、会议、政策一律算 not_event:industry）
anomaly_psychic=异常/灵能（UFO/UAP、灵异、超自然现象；有可核验的天文航天主体时应归 neo/深空）
other=以上都不属于

【content_nature 必须填，取六值之一】
科学事实=有同行评议或官方发布 / 科研进展=论文或机构发布尚未定论 /
未证实目击=目击报告或单一信源 / 灵异传闻=灵异超自然叙述 /
娱乐化灵异=娱乐号综艺段子手包装的灵异内容 / 伪科学主张=有明确科学主张但无依据

【confidence】0 到 1 的小数，表示你对 is_event 判定的把握。

只输出 JSON object，字段固定为：
{"is_event": true, "event_type": "bci_consciousness", "category": "clinical_breakthrough",
 "content_nature": "科研进展", "confidence": 0.86, "reason": "一句话理由"}
不是事件时：event_type 填 null，category 填六值之一。"""


class AiJudgement(BaseModel):
    """AI 输出 schema（05 §6.4）的严格校验：非法值一律视为失败 → pending。"""

    is_event: bool
    event_type: str | None = None
    category: str | None = None
    content_nature: str | None = None
    confidence: float
    reason: str = ""


@dataclass
class JudgeResult:
    is_event: bool | None
    event_type: str | None
    content_nature: str | None
    confidence: float | None
    risk_flags: list[str]
    status: str
    reject_reason: str | None
    priority: int
    # 以下是给 run_preprocess_batch 写 ai_run 用的旁路信息，不参与判定
    event_time: datetime | None = None
    ai_status: str = "success"
    ai_error: str | None = None
    tokens: int = 0
    latency_ms: int = 0
    input_hash: str = ""
    output: dict = field(default_factory=dict)


def _hint(filter_result: FilterResult) -> str:
    """05 §6.2 的规则预判：**不改 status，只决定怎么问 AI**。"""
    if filter_result.whitelisted and filter_result.soft is None:
        return "likely_event"
    if not filter_result.anchors and filter_result.soft is not None:
        return "likely_not_event"
    if len(filter_result.cleaned_title) < 8:
        return "likely_not_event"
    return "uncertain"


def _build_payload(
    *,
    title: str,
    summary: str | None,
    publisher_name: str | None,
    source_level: str,
    filter_result: FilterResult,
) -> dict:
    payload: dict = {
        "instructions": _INSTRUCTIONS,
        "title": title,
        "summary": (summary or "")[:SUMMARY_LIMIT],
        "publisher_name": publisher_name or "",
        "source_level": source_level,
    }
    hint = _hint(filter_result)
    if hint == "likely_event":
        payload["hint"] = "规则预判：标题同时命中栏目锚定词与事件动词，很可能是一条事件。"
    elif hint == "likely_not_event":
        payload["hint"] = "规则预判：没有命中栏目锚定词/事件动词，或标题过短，请先判断它是不是事件。"
    return payload


def _parse_event_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def judge_from_data(
    data: dict, *, filter_result: FilterResult, source_level: str
) -> JudgeResult:
    """落点判定表（05 §6.5 的 6 行，逐条实现）。**纯函数**，单测直接调它。"""
    try:
        parsed = AiJudgement.model_validate(data)
    except ValidationError as exc:
        return JudgeResult(
            is_event=None,
            event_type=None,
            content_nature=None,
            confidence=None,
            risk_flags=[],
            status="pending",
            reject_reason=None,
            priority=filter_result.priority,
            ai_status="failed",
            ai_error=f"schema: {exc.errors()[:1]}",
            output=data,
        )

    event_type = parsed.event_type
    if event_type is not None:
        event_type = event_type.strip()
        if event_type not in EVENT_TYPES:
            # 非法枚举值 = 不可信输出，按失败处理（不猜）
            return JudgeResult(
                is_event=None,
                event_type=None,
                content_nature=None,
                confidence=None,
                risk_flags=[],
                status="pending",
                reject_reason=None,
                priority=filter_result.priority,
                ai_status="failed",
                ai_error=f"event_type 非法：{event_type}",
                output=data,
            )

    nature = parsed.content_nature
    if nature is not None:
        nature = nature.strip()
        if nature not in CONTENT_NATURES:
            return JudgeResult(
                is_event=None,
                event_type=None,
                content_nature=None,
                confidence=None,
                risk_flags=[],
                status="pending",
                reject_reason=None,
                priority=filter_result.priority,
                ai_status="failed",
                ai_error=f"content_nature 非法：{nature}",
                output=data,
            )

    confidence = max(0.0, min(1.0, float(parsed.confidence)))
    category = (parsed.category or "").strip().lower()

    # ---- 第 3 行：灵异四类 → pending + risk_flags=['supernatural'] ----
    if parsed.is_event and nature in SUPERNATURAL_NATURES:
        return JudgeResult(
            is_event=True,
            event_type=event_type,
            content_nature=nature,
            confidence=confidence,
            risk_flags=["supernatural"],
            status="pending",
            reject_reason=None,
            priority=filter_result.priority - SUPERNATURAL_PENALTY,
            event_time=_parse_event_time(data.get("event_time")),
            output=data,
        )

    if parsed.is_event:
        # ---- 第 2 行：C 级源 → pending（C 不是丢弃，是进待定池）----
        if source_level == "C":
            status = "pending"
        # ---- 第 4 行：置信度不足 → pending ----
        elif confidence < settings.EVENT_CONFIDENCE_MIN:
            status = "pending"
        # ---- 第 1 行：A/B 级 + 高置信 + 非灵异 → candidate ----
        elif source_level in ("A", "B") and nature in ("科学事实", "科研进展"):
            status = "candidate"
        else:
            # content_nature 缺失等 §6.5 未覆盖的组合：fail-safe 落待定池
            status = "pending"
        return JudgeResult(
            is_event=True,
            event_type=event_type,
            content_nature=nature,
            confidence=confidence,
            risk_flags=[],
            status=status,
            reject_reason=None,
            priority=filter_result.priority,
            event_time=_parse_event_time(data.get("event_time")),
            output=data,
        )

    # ---- 第 5 行：判非事件且高置信 → 拒 ----
    if confidence >= settings.NOT_EVENT_CONFIDENCE_MIN:
        reason_category = category if category in NOT_EVENT_CATEGORIES else "other"
        return JudgeResult(
            is_event=False,
            event_type=None,
            content_nature=nature,
            confidence=confidence,
            risk_flags=[],
            status="rule_rejected",
            reject_reason=f"not_event:{reason_category}",
            priority=filter_result.priority,
            output=data,
        )

    # ---- 第 6 行：判非事件但没把握 → pending（判不准不丢）----
    return JudgeResult(
        is_event=None,
        event_type=None,
        content_nature=nature,
        confidence=confidence,
        risk_flags=[],
        status="pending",
        reject_reason=None,
        priority=filter_result.priority,
        output=data,
    )


def stage_judge(
    *,
    title: str,
    summary: str | None,
    publisher_name: str | None,
    source_level: str,
    filter_result: FilterResult,
) -> JudgeResult:
    """L4/L5：调 AI 拿结构化结果，再按落点表定 status。**不碰数据库**。"""
    payload = _build_payload(
        title=title,
        summary=summary,
        publisher_name=publisher_name,
        source_level=source_level,
        filter_result=filter_result,
    )
    hash_value = ai.input_hash(
        payload, model=settings.LLM_MODEL_CLASSIFY, prompt_version=settings.PROMPT_VERSION
    )
    result = ai.classify(payload, prompt_version=settings.PROMPT_VERSION)

    if result.status != "success" or not result.data:
        # AI 失败 / 跳过 / 空 dict → pending + is_event=NULL（绝不默认拒，05 §6.5 第 6 行）
        return JudgeResult(
            is_event=None,
            event_type=None,
            content_nature=None,
            confidence=None,
            risk_flags=[],
            status="pending",
            reject_reason=None,
            priority=filter_result.priority,
            ai_status=result.status,
            ai_error=result.error,
            tokens=result.tokens,
            latency_ms=result.latency_ms,
            input_hash=hash_value,
            output={},
        )

    judged = judge_from_data(result.data, filter_result=filter_result, source_level=source_level)
    judged.tokens = result.tokens
    judged.latency_ms = result.latency_ms
    judged.input_hash = hash_value
    judged.ai_error = result.error
    if judged.ai_status == "success" and result.status != "success":
        judged.ai_status = result.status
    return judged


# --------------------------------------------------------------------------- #
# 批量执行
# --------------------------------------------------------------------------- #


def _fetch_batch(session: Session, batch: int) -> list[tuple[Article, RawItem]]:
    rows = session.execute(
        select(Article, RawItem)
        .join(RawItem, Article.raw_item_id == RawItem.id)
        .where(Article.status == "ingested")
        .order_by(Article.id.asc())
        .limit(batch)
    ).all()
    return [(row[0], row[1]) for row in rows]


def count_pending(session: Session) -> int:
    """Worker 每 60 s 只想知道「有没有 ingested 条目」。"""
    return int(
        session.execute(
            select(func.count()).select_from(Article).where(Article.status == "ingested")
        ).scalar_one()
        or 0
    )


def _write_judgement(article: Article, judged: JudgeResult) -> None:
    article.is_event = judged.is_event
    article.event_type = judged.event_type
    article.content_nature = judged.content_nature
    article.status = judged.status
    article.reject_reason = judged.reject_reason
    article.review_priority = judged.priority
    article.risk_flags = list(judged.risk_flags)
    article.event_confidence = judged.confidence
    if judged.event_time is not None:
        article.event_time = judged.event_time


def run_preprocess_batch(limit: int | None = None) -> dict:
    """跑一批（默认 ``PREPROCESS_BATCH_SIZE`` 条）。返回 stats dict。

    单条异常不中断整批；**DB 写入全部在主线程串行**，AI 调用可并发。
    """
    batch = int(limit if limit is not None else settings.PREPROCESS_BATCH_SIZE)
    stats = {
        "scanned": 0,
        "cleaned": 0,
        "rule_rejected": 0,
        "not_event": 0,
        "candidate": 0,
        "pending": 0,
        "ai_called": 0,
        "ai_failed": 0,
        "tokens": 0,
    }
    if batch <= 0:
        return stats

    session = SessionLocal()
    run_id: int | None = None
    try:
        run = TaskRun(
            task_type="preprocess",
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
        logger.exception("建 preprocess 任务失败")
        session.close()
        return stats

    try:
        rules = load_rules(session)
        rows = _fetch_batch(session, batch)
        logger.info(
            "预处理开始 task_run=%s 本批 %s 条（硬排除 %s 组 / 软扣分 %s 组 / 锚定 %s 组）",
            run_id,
            len(rows),
            len(rules.hard),
            len(rules.soft),
            len(rules.anchors),
        )

        # —— 阶段 A（主线程）：L1 清洗 + L2/L3 过滤 ——
        # candidates = (article, 送给 stage_judge 的纯值参数, FilterResult)
        candidates: list[tuple[Article, dict, FilterResult]] = []
        for article, raw in rows:
            try:
                original = article.title
                publisher = raw.publisher_name or article.publisher_name
                summary = raw.raw_summary or article.summary
                pre_cleaned = clean_title(article.title, publisher)
                filter_result = stage_filter(
                    pre_cleaned,
                    summary,
                    source_level=article.source_level,
                    rules=rules,
                )
                stats["scanned"] += 1
                article.title = filter_result.cleaned_title
                if filter_result.cleaned_title != original:
                    stats["cleaned"] += 1

                if filter_result.hard is not None:
                    category, word = filter_result.hard
                    article.status = "rule_rejected"
                    article.reject_reason = f"rule:{category}:{word}"
                    article.review_priority = filter_result.priority
                    stats["rule_rejected"] += 1
                    session.commit()
                    continue

                # ★ 06 即时去重的插入点 ★
                # 05 §2 / §9：硬排除之后、AI 判定之前先置 processing，
                # 06 的去重逻辑就插在这一步与 stage_judge 之间
                # （同一 event_cluster 内已有时效窗口内的条目 → status='duplicate'，不花 AI 的钱）。
                # 本期 06 未落地，这里只落到 processing 就继续判定。
                article.status = "processing"
                session.commit()
                candidates.append(
                    (
                        article,
                        {
                            "title": filter_result.cleaned_title,
                            "summary": summary,
                            "publisher_name": publisher,
                            "source_level": article.source_level,
                            "filter_result": filter_result,
                        },
                        filter_result,
                    )
                )
            except Exception:
                session.rollback()
                logger.exception("预处理单条失败 article_id=%s（已跳过）", article.id)

        # —— 阶段 B：AI 并发调用（线程里只有纯值，不碰 ORM、不碰数据库）——
        if candidates:
            judged_by_id: dict[int, JudgeResult] = {}
            workers = max(1, int(settings.PREPROCESS_CONCURRENCY))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    article.id: pool.submit(stage_judge, **judge_kwargs)
                    for article, judge_kwargs, _filter_result in candidates
                }
                for article, _judge_kwargs, _filter_result in candidates:
                    try:
                        judged_by_id[article.id] = futures[article.id].result()
                    except Exception:
                        logger.exception("AI 判定线程异常 article_id=%s", article.id)

            # —— 阶段 C（主线程）：落库串行 ——
            for article, _judge_kwargs, _filter_result in candidates:
                judged = judged_by_id.get(article.id)
                if judged is None:
                    continue
                try:
                    _write_judgement(article, judged)
                    stats["ai_called"] += 1
                    stats["tokens"] += judged.tokens
                    if judged.ai_status != "success":
                        stats["ai_failed"] += 1
                    if judged.status == "candidate":
                        stats["candidate"] += 1
                    elif judged.status == "rule_rejected":
                        stats["not_event"] += 1
                    else:
                        stats["pending"] += 1
                    session.add(
                        AiRun(
                            article_id=article.id,
                            stage="judge",
                            model=settings.LLM_MODEL_CLASSIFY,
                            prompt_version=settings.PROMPT_VERSION,
                            input_hash=judged.input_hash,
                            output_json=judged.output or None,
                            tokens=judged.tokens,
                            latency_ms=judged.latency_ms,
                            status=judged.ai_status,
                            error=(judged.ai_error[:2000] if judged.ai_error else None),
                        )
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                    logger.exception("预处理回写失败 article_id=%s（已跳过）", article.id)
    except Exception:
        session.rollback()
        logger.exception("预处理批次异常 task_run=%s", run_id)
    finally:
        session.close()

    _close_task_run(run_id, stats)
    logger.info(
        "预处理完成 task_run=%s scanned=%s cleaned=%s rule_rejected=%s "
        "not_event=%s candidate=%s pending=%s ai_called=%s ai_failed=%s tokens=%s",
        run_id,
        stats["scanned"],
        stats["cleaned"],
        stats["rule_rejected"],
        stats["not_event"],
        stats["candidate"],
        stats["pending"],
        stats["ai_called"],
        stats["ai_failed"],
        stats["tokens"],
    )
    return stats
def _close_task_run(run_id: int | None, stats: dict) -> None:
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
        logger.exception("收口 preprocess 任务失败 task_run_id=%s", run_id)
    finally:
        session.close()
