"""预处理与事件判定单测（05 §10.3）。

**全程不调真实 LLM**：AI 层用假的 OpenAI client（``monkeypatch``），
判定表直接对 ``judge_from_data`` 造 dict；词表从 ``fixtures/keyword_rule_seed.yaml``
经 ``seed_keyword_rules`` + ``load_rules`` 真实入库后再编译，顺带验证种子文件。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.keyword import KeywordRule
from app.seed import seed_keyword_rules
from app.services import ai
from app.services.preprocess import (
    FilterResult,
    clean_title,
    judge_from_data,
    load_rules,
    stage_filter,
    stage_judge,
)

# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #


@pytest.fixture()
def rules(session):
    """把 seed 词表灌进测试库再编译成 RuleSet（= 线上同一条路径）。"""
    seed_keyword_rules(session)
    return load_rules(session)


def _filter(**kwargs) -> FilterResult:
    base = dict(
        cleaned_title="某标题",
        hard=None,
        soft=None,
        anchors=set(),
        verbs=[],
        signals=[],
        whitelisted=False,
        priority=40,
    )
    base.update(kwargs)
    return FilterResult(**base)


class _FakeCompletions:
    """按顺序吐出预设 content，并记录调用次数（验重试）。"""

    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls = 0

    def create(self, **_kwargs):
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(total_tokens=42),
        )


def _patch_ai(monkeypatch, contents: list[str]) -> _FakeCompletions:
    completions = _FakeCompletions(contents)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(ai, "OpenAI", lambda **_kwargs: fake)
    monkeypatch.setattr(ai.settings, "DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(ai.settings, "REPLAY_MODE", False)
    return completions


# --------------------------------------------------------------------------- #
# L1 清洗（05 §4.4）
# --------------------------------------------------------------------------- #


def test_clean_title_strips_media_tail():
    assert (
        clean_title("四肢瘫痪受试者植入脑机接口玩转GTA6 - Jiemian.com", None)
        == "四肢瘫痪受试者植入脑机接口玩转GTA6"
    )


def test_clean_title_keeps_numbers_before_media_tail():
    """反例：按 `-` 简单截断会把 `1.18亿元` 丢掉。"""
    cleaned = clean_title(
        "完美世界(002624.SZ)：上半年净亏损1.18亿元 - TradingView", None
    )
    assert cleaned.endswith("1.18亿元")
    assert "TradingView" not in cleaned


def test_clean_title_strips_publisher_tail():
    assert clean_title("暗物质探测新进展 - 新华社", "新华社") == "暗物质探测新进展"


def test_clean_title_strips_stacked_noise():
    assert (
        clean_title("魔兽世界【无限服】副本门钥匙任务大盘点 - Sohu", "Sohu")
        == "魔兽世界副本门钥匙任务大盘点"
    )


def test_clean_title_leaves_plain_title_alone():
    assert clean_title("NASA 发现近地小行星", None) == "NASA 发现近地小行星"


# --------------------------------------------------------------------------- #
# L2 硬排除：每条正则至少一个实采例句
# --------------------------------------------------------------------------- #

HARD_SAMPLES = [
    # finance：股票代码（05 §4.1 表第 1 行）
    ("finance", "完美世界(002624.SZ)：上半年净亏损1.18亿元"),
    # finance：概念股/机构调研
    ("finance", "多只概念股获机构扎堆调研(附股) - Sohu"),
    # game：副本/试玩
    ("game", "《魔兽世界》副本门钥匙任务大盘点 - Sohu"),
    ("game", "《Quack Hunters》试玩版公开 - Sohu"),
    # entertain：本批 0 命中，用需求点名的影视类补充例句
    ("entertain", "国产科幻电影《流浪地球3》首支预告片发布"),
    # divination：本批 0 命中，用需求点名的星座命理类补充例句
    ("divination", "10月26日今日运程：十二星座运势一览"),
]


@pytest.mark.parametrize("expect_category,title", HARD_SAMPLES)
def test_hard_rule_hits_sample(rules, expect_category, title):
    result = stage_filter(title, None, source_level="C", rules=rules)
    assert result.hard is not None, f"未命中硬排除：{title}"
    assert result.hard[0] == expect_category


# --------------------------------------------------------------------------- #
# 三条已知误杀回归（05 §1 ③ / §10.3）
# --------------------------------------------------------------------------- #


def test_how_question_not_hard_rejected(rules):
    """光明网深度报道含「如何」——「如何」根本不在硬排除表里。"""
    result = stage_filter(
        "科技观察｜脑机接口走向临床，如何证明“非它不可”？",
        None,
        source_level="A",
        rules=rules,
    )
    assert result.hard is None


def test_gta6_title_not_hard_rejected(rules):
    """Jiemian 那条是 BCI 里程碑事件，不能被 `游戏` 类词误杀。"""
    result = stage_filter(
        "四肢瘫痪受试者植入脑机接口玩转GTA6 - Jiemian.com",
        None,
        source_level="B",
        rules=rules,
    )
    assert result.hard is None
    assert "bci_consciousness" in result.anchors


def test_conference_title_soft_only_and_whitelisted(rules):
    """新华网「大会」那条：只软扣分，且锚定词 + 事件动词把它救回来。"""
    result = stage_filter(
        "2026腾冲科学家论坛将首次举办脑机接口等特色专题活动",
        None,
        source_level="A",
        rules=rules,
    )
    assert result.hard is None
    assert result.soft == ("industry", -3)
    assert result.whitelisted is True
    # A(30) + 锚定 1 类(5) + 信号「首次」(2)，软扣分被白名单清零 → 37
    assert result.priority == 37


def test_whitelist_cancels_soft_penalty(rules):
    """同一个标题去掉事件动词后，软扣分就生效。"""
    with_verb = stage_filter("脑机接口大会宣布最新成果", None, source_level="B", rules=rules)
    without_verb = stage_filter("脑机接口大会", None, source_level="B", rules=rules)
    assert with_verb.whitelisted is True
    assert without_verb.whitelisted is False
    assert without_verb.priority == 15 + 5 - 3


# --------------------------------------------------------------------------- #
# 拉丁缩写边界（05 §3 第 3 条 / fixtures 注释）
# --------------------------------------------------------------------------- #


def test_neo_word_boundary(rules):
    result = stage_filter(
        "Fermented milk may have held ritual power in Neolithic Denmark",
        None,
        source_level="A",
        rules=rules,
    )
    assert "neo" not in result.anchors

    hit = stage_filter("NASA spots a new NEO approaching Earth", None, source_level="A", rules=rules)
    assert "neo" in hit.anchors


# --------------------------------------------------------------------------- #
# review_priority 公式（05 §8）
# --------------------------------------------------------------------------- #


def test_priority_formula_two_anchors_two_signals(rules):
    """A(30) + 2 个锚定类(10) + 2 个信号词(4) = 44。"""
    result = stage_filter(
        "宇宙深空里的脑机接口与暗物质", None, source_level="A", rules=rules
    )
    assert result.anchors == {"bci_consciousness", "deep_space_particle"}
    assert result.signals == ["宇宙", "深空"]
    assert result.priority == 44


# --------------------------------------------------------------------------- #
# 落点判定表 05 §6.5（6 行各一条）
# --------------------------------------------------------------------------- #

EVENT_OK = {
    "is_event": True,
    "event_type": "bci_consciousness",
    "category": "clinical_breakthrough",
    "content_nature": "科研进展",
    "confidence": 0.86,
    "reason": "有主体、动作、时间，可核验",
}


def test_row1_event_high_confidence_ab_level_is_candidate():
    judged = judge_from_data(EVENT_OK, filter_result=_filter(), source_level="A")
    assert judged.status == "candidate"
    assert judged.is_event is True
    assert judged.event_type == "bci_consciousness"


def test_row2_event_c_level_is_pending():
    judged = judge_from_data(EVENT_OK, filter_result=_filter(), source_level="C")
    assert judged.status == "pending"


def test_row3_event_supernatural_is_pending_with_flag():
    judged = judge_from_data(
        {**EVENT_OK, "event_type": "anomaly_psychic", "content_nature": "灵异传闻"},
        filter_result=_filter(priority=40),
        source_level="A",
    )
    assert judged.status == "pending"
    assert "supernatural" in judged.risk_flags
    assert judged.priority == 30  # 40 - 灵异惩罚 10


def test_row4_event_low_confidence_is_pending():
    judged = judge_from_data(
        {**EVENT_OK, "confidence": 0.5}, filter_result=_filter(), source_level="A"
    )
    assert judged.status == "pending"


def test_row5_not_event_high_confidence_is_rejected():
    judged = judge_from_data(
        {
            "is_event": False,
            "event_type": None,
            "category": "industry",
            "content_nature": "科研进展",
            "confidence": 0.9,
            "reason": "行业会议动态",
        },
        filter_result=_filter(),
        source_level="A",
    )
    assert judged.status == "rule_rejected"
    assert judged.reject_reason == "not_event:industry"


def test_row6_not_event_low_confidence_is_pending():
    judged = judge_from_data(
        {
            "is_event": False,
            "event_type": None,
            "category": "industry",
            "content_nature": None,
            "confidence": 0.6,
            "reason": "拿不准",
        },
        filter_result=_filter(),
        source_level="A",
    )
    assert judged.status == "pending"
    assert judged.is_event is None


@pytest.mark.parametrize(
    "nature", ["未证实目击", "灵异传闻", "娱乐化灵异", "伪科学主张"]
)
def test_supernatural_natures_always_pending(nature):
    judged = judge_from_data(
        {**EVENT_OK, "event_type": "anomaly_psychic", "content_nature": nature},
        filter_result=_filter(),
        source_level="A",
    )
    assert judged.status == "pending"
    assert "supernatural" in judged.risk_flags


def test_illegal_event_type_is_treated_as_failure():
    judged = judge_from_data(
        {**EVENT_OK, "event_type": "ufo_magic"}, filter_result=_filter(), source_level="A"
    )
    assert judged.status == "pending"
    assert judged.is_event is None
    assert judged.ai_status == "failed"


# --------------------------------------------------------------------------- #
# AI 层：围栏剥离 / 非法 JSON / 空 dict（AI编码对照 §2.5）
# --------------------------------------------------------------------------- #


def _judge_kwargs(rules):
    return dict(
        title="浙大团队发布面向难治性抑郁症治疗的脑机接口系统",
        summary="临床试验取得突破",
        publisher_name="新华网",
        source_level="A",
        filter_result=_filter(priority=35),
    )


def test_fenced_response_is_parsed(rules, monkeypatch):
    fenced = '```json\n{"is_event": true, "event_type": "bci_consciousness", ' \
             '"category": "clinical_breakthrough", "content_nature": "科研进展", ' \
             '"confidence": 0.86, "reason": "有临床试验结果"}\n```'
    _patch_ai(monkeypatch, [fenced])
    judged = stage_judge(**_judge_kwargs(rules))
    assert judged.ai_status == "success"
    assert judged.status == "candidate"
    assert judged.tokens == 42


def test_invalid_json_retries_once_then_pending(rules, monkeypatch):
    completions = _patch_ai(monkeypatch, ["not json at all"])
    judged = stage_judge(**_judge_kwargs(rules))
    assert completions.calls == 2  # 1 次 + 重试 1 次
    assert judged.status == "pending"
    assert judged.is_event is None
    assert judged.ai_status == "failed"


def test_empty_dict_falls_back_to_pending(rules, monkeypatch):
    _patch_ai(monkeypatch, ["{}"])
    judged = stage_judge(**_judge_kwargs(rules))
    assert judged.status == "pending"
    assert judged.is_event is None


def test_classify_without_key_is_skipped(rules, monkeypatch):
    monkeypatch.setattr(ai.settings, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(ai.settings, "REPLAY_MODE", False)
    judged = stage_judge(**_judge_kwargs(rules))
    assert judged.ai_status == "skipped"
    assert judged.status == "pending"
    assert judged.is_event is None


# --------------------------------------------------------------------------- #
# seed 幂等（05 §3 / 03 §10.2）
# --------------------------------------------------------------------------- #


def test_seed_keyword_rules_is_idempotent(session):
    first = seed_keyword_rules(session)
    second = seed_keyword_rules(session)
    assert first == second == 15
    assert session.query(KeywordRule).count() == 15
