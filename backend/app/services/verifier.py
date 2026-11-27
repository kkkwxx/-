"""确定性轻校验与风险评分器（需求文档 v1.0 §7 / §8 / §11）。

设计原则：
- **纯 Python 确定性规则，零 LLM 依赖**，既用于 AI 生成后的自动轻校验与重生成决策，
  也用于人工审核工作台「编辑后实时重校验」。
- 覆盖：
  1. 标题红线词硬拦截（不得写“已确认外星人”“地球将毁灭”“人类已掌握”等）；
  2. 标题禁止解释后缀（禁止出现“原来是”“别怕，这是”等剧透拼接）；
  3. 标题去标点字数校验（B-05 拍板：7—20 字放行，推荐 10—18 字）；
  4. 导语 ``body_lead``（20—65 字）与摘要 ``body_summary``（70—140 字）字数和套话检查；
  5. 灵异/异常强约束：正文必须带娱乐/未经证实限定语，不得写成科学结论。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.core.config import settings

# 去除所有标点与空白后统计“有效字数”（中文字 + 英文数字词按字符计）
_PUNCT_WS_RE = re.compile(r"[\s\W_]+", re.UNICODE)
# 零宽字符与空白/常见分隔符号，用于在红线词匹配前归一化防绕过
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]+")
_COLLAPSE_FOR_RISK_RE = re.compile(r"[\s\u3000\-_·•|/\\,，。！？!?：:;；'\"“”‘’()（）\[\]【】]+")

# 需求 §7.1 底线：绝不允许出现在标题或正文中的无依据绝对化表达（均开启 re.I，对归一化文本匹配）
PROHIBITED_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("已确认外星人", re.compile(r"已确认外星人|外星人已确认|外星人证实|证实外星人|外星人真的来了", re.I)),
    ("地球将毁灭", re.compile(r"地球将毁灭|地球要毁灭|地球要完|世界末日|人类将灭绝", re.I)),
    ("政府隐瞒真相", re.compile(r"政府隐瞒|官方隐瞒|掩盖真相|nasa隐瞒", re.I)),
    ("实验夸大为已掌握", re.compile(r"人类已掌握|彻底掌握意念|真修仙成功|已经攻克", re.I)),
)

# 需求 §7.1：不要在标题后加解释句、简述后缀
TITLE_SPOILER_RE = re.compile(
    r"原来是|其实是|别怕|真相是|专家解释|官方回应称|其实只是", re.I
)

# 需求 §7.2：不堆“据悉”“日前”“相关研究显示”等空话
CLICHE_RE = re.compile(r"据悉|日前|相关研究显示|近日记者获悉|有关专家表示")

# 需求 §5.3 / §7.2 / §10：灵异/异常类正文必须包含明确的未证实/娱乐免责限定语（收紧，防止单字“综艺/传闻”误放行）
SUPERNATURAL_QUALIFIER_RE = re.compile(
    r"娱乐节目|别当真|未经科学证实|未经证实|尚未证实|网络讨论|目击传闻|无法证实|暂无证据|尚无实证|切勿当真|非科学结论"
)


def _normalize_for_risk(text: str | None) -> str:
    """NFKC 归一化 + 清零宽字符 + 折叠空白与标点，防止插空格/大小写绕过红线检测。"""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text)
    norm = _ZERO_WIDTH_RE.sub("", norm)
    return _COLLAPSE_FOR_RISK_RE.sub("", norm)

# 需求 §7.1 可用风格词（命中可作为选优加分参考）
STYLE_POWER_WORDS: tuple[str, ...] = (
    "鬼", "幽灵", "外星人", "地球2.0", "贴脸", "直扑", "擦肩", "杀进", "炸场",
    "封神", "翻车", "降维打击", "活久见", "细思极恐", "现形", "逮到", "有戏",
    "科幻成真", "不是科幻", "不用手", "驭剑",
)

SUPERNATURAL_NATURES = frozenset({"未证实目击", "灵异传闻", "娱乐化灵异", "伪科学主张"})

# 灵异类发布快照默认免责声明
DEFAULT_SUPERNATURAL_DISCLAIMER = (
    "提示：本文涉及娱乐节目、网络讨论或未经科学证实的异常现象，仅供趣味阅读，请理性看待，切勿当真。"
)


def count_effective_chars(text: str | None) -> int:
    """计算去掉中英文标点和空白后的有效字符数（B-05 口径）。"""
    if not text:
        return 0
    norm = unicodedata.normalize("NFKC", text)
    return len(_PUNCT_WS_RE.sub("", norm))


def count_display_chars(text: str | None) -> int:
    """计算去掉首尾空白后的展示字数（用于导语/摘要区间校验）。"""
    if not text:
        return 0
    return len(re.sub(r"\s+", "", text.strip()))


@dataclass
class TitleCheckResult:
    passed: bool
    effective_chars: int
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    has_style_word: bool = False


@dataclass
class ContentCheckResult:
    passed: bool
    hard_failed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)


def verify_title(
    title: str | None,
    *,
    title_min_chars: int | None = None,
    title_max_chars: int | None = None,
) -> TitleCheckResult:
    """校验单条候选标题是否合规。"""
    text = (title or "").strip()
    eff = count_effective_chars(text)
    errors: list[str] = []
    flags: list[str] = []

    if not text:
        return TitleCheckResult(passed=False, effective_chars=0, errors=["标题为空"], flags=["empty_title"])

    min_c = int(title_min_chars if title_min_chars is not None else settings.TITLE_MIN_CHARS)
    max_c = int(title_max_chars if title_max_chars is not None else settings.TITLE_MAX_CHARS)
    if eff < min_c or eff > max_c:
        errors.append(f"标题有效字数 {eff} 字不在 {min_c}—{max_c} 字范围内")
        flags.append("title_length_invalid")

    folded = _normalize_for_risk(text)
    if TITLE_SPOILER_RE.search(folded):
        errors.append("标题包含解释性后缀（如“原来是/别怕”），只应保留悬念前半句")
        flags.append("title_has_spoiler")

    for label, pattern in PROHIBITED_CLAIM_PATTERNS:
        if pattern.search(folded):
            errors.append(f"标题命中禁止红线词：{label}")
            flags.append("prohibited_claim")

    has_style = any(word in text for word in STYLE_POWER_WORDS)
    return TitleCheckResult(
        passed=len(errors) == 0,
        effective_chars=eff,
        errors=errors,
        flags=list(dict.fromkeys(flags)),
        has_style_word=has_style,
    )


def verify_content(
    *,
    title_final: str | None,
    body_lead: str | None,
    body_summary: str | None,
    fact_anchor: str | None = None,
    event_type: str | None = None,
    content_nature: str | None = None,
    source_level: str | None = None,
    title_min_chars: int | None = None,
    title_max_chars: int | None = None,
) -> ContentCheckResult:
    """对最终标题 + 导语 + 摘要执行完整确定性轻校验。"""
    errors: list[str] = []
    warnings: list[str] = []
    risk_flags: list[str] = []
    hard_failed = False

    # 1. 标题校验
    t_res = verify_title(
        title_final,
        title_min_chars=title_min_chars,
        title_max_chars=title_max_chars,
    )
    if not t_res.passed:
        errors.extend(t_res.errors)
        risk_flags.extend(t_res.flags)
        if "prohibited_claim" in t_res.flags or "empty_title" in t_res.flags:
            hard_failed = True

    # 2. 正文第一段 body_lead（需求 §7.2：20—60 字，宽容到 18—75 字作硬阈值）
    lead = (body_lead or "").strip()
    lead_len = count_display_chars(lead)
    if not lead:
        errors.append("缺少正文第一段（body_lead）")
        risk_flags.append("missing_body_lead")
        hard_failed = True
    elif lead_len < 18 or lead_len > 75:
        errors.append(f"正文第一段 {lead_len} 字超出建议范围（20—60 字）")
        risk_flags.append("body_lead_length")

    # 3. 正文摘要 body_summary（需求 §7.2：80—120 字，宽容到 60—160 字）
    summary = (body_summary or "").strip()
    sum_len = count_display_chars(summary)
    if not summary:
        errors.append("缺少正文大白话摘要（body_summary）")
        risk_flags.append("missing_body_summary")
        hard_failed = True
    elif sum_len < 55 or sum_len > 170:
        warnings.append(f"正文摘要 {sum_len} 字偏离 80—120 字目标区间")
        risk_flags.append("body_summary_length")

    # 4. 正文红线词检查（对归一化折叠文本匹配，防止插空格/零宽字符/大小写绕过）
    full_body = f"{lead}\n{summary}"
    folded_body = _normalize_for_risk(full_body)
    for label, pattern in PROHIBITED_CLAIM_PATTERNS:
        if pattern.search(folded_body):
            errors.append(f"正文命中禁止红线词：{label}")
            risk_flags.append("prohibited_claim")
            hard_failed = True

    # 5. 空话套话检测
    if CLICHE_RE.search(full_body):
        warnings.append("正文包含“据悉/日前/相关研究显示”等公文套话，建议改为大白话")
        risk_flags.append("bureaucratic_cliche")

    # 6. 灵异/异常类强制限定语检查（需求 §5.3 / §11）
    is_supernatural = (
        event_type == "anomaly_psychic" or content_nature in SUPERNATURAL_NATURES
    )
    if is_supernatural:
        risk_flags.append("supernatural")
        if not SUPERNATURAL_QUALIFIER_RE.search(full_body):
            errors.append("灵异/异常类正文必须注明“娱乐节目/未经证实/网络讨论/别当真”等限定属性")
            risk_flags.append("missing_supernatural_disclaimer")
            hard_failed = True

    # 7. C 级信源风险提示
    if source_level == "C":
        risk_flags.append("c_level_source")

    # 8. 事实锚点缺失提示
    if not (fact_anchor or "").strip():
        warnings.append("缺少事实锚点（fact_anchor），审核时请对照原文核验")
        risk_flags.append("missing_fact_anchor")

    dedup_flags = list(dict.fromkeys(risk_flags))
    return ContentCheckResult(
        passed=len(errors) == 0,
        hard_failed=hard_failed,
        errors=errors,
        warnings=warnings,
        risk_flags=dedup_flags,
    )


def default_disclaimer_for(event_type: str | None, content_nature: str | None) -> str | None:
    """为灵异/异常类内容生成默认免责声明。"""
    if event_type == "anomaly_psychic" or content_nature in SUPERNATURAL_NATURES:
        return DEFAULT_SUPERNATURAL_DISCLAIMER
    return None
