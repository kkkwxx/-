"""域名归一 + 定级六步判定链（03 §6）。纯函数，不碰数据库与网络。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from app.services.domain import registrable_domain
from app.services.dto import RawItemDraft, SourceConfig
from app.services.grading import grade_article_source


def _item(**kwargs) -> RawItemDraft:
    defaults = dict(
        source_id=1,
        url="https://example.com/a",
        title="标题",
        fetched_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return RawItemDraft(**defaults)


def _source(**kwargs) -> SourceConfig:
    defaults = dict(id=1, code="s", type="rss", level="B", expected_domains=())
    defaults.update(kwargs)
    return SourceConfig(**defaults)


# --------------------------------------------------------------------------- #
# §6.7 域名归一
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("news.bbc.co.uk", "bbc.co.uk"),
        ("www.nasa.gov", "nasa.gov"),
        ("NEWS.Sciencenet.CN", "sciencenet.cn"),
        ("notnasa.gov", "notnasa.gov"),
        ("https://phys.org/rss-feed/space-news/", "phys.org"),
        ("https://news.google.com/rss/articles/CBMi?oc=5", "google.com"),
        ("https://www.cast.org.cn", "cast.org.cn"),
        ("https://mp.weixin.qq.com/s/abc", "qq.com"),
        # 单标签主机（localhost）没有注册域，归一为 None → 定级落 rule1:no-origin
        ("http://localhost:8000/feed", None),
        ("", None),
        (None, None),
    ],
)
def test_registrable_domain(raw, expected):
    assert registrable_domain(raw) == expected


def test_seed_domains_are_already_registrable():
    """种子里的域名必须是规整的 eTLD+1，否则规则永远命中不了。"""
    seed = Path(__file__).resolve().parents[2] / "fixtures" / "domain_rule_seed.yaml"
    rules = yaml.safe_load(seed.read_text(encoding="utf-8"))
    assert rules
    bad = [r["domain"] for r in rules if registrable_domain(r["domain"]) != r["domain"]]
    assert bad == []


# --------------------------------------------------------------------------- #
# 六步判定链
# --------------------------------------------------------------------------- #

def test_rule1_aggregator_never_falls_back_to_url():
    """P0 回归：聚合源只能取 publisher_url，绝不拿 url 兜底。"""
    item = _item(url="https://news.google.com/rss/articles/CBMi", publisher_url=None)
    result = grade_article_source(item, _source(type="google_news"), rules={"nasa.gov": "A"})
    assert result.level == "C"
    assert result.reason == "rule1:no-origin"


def test_rule1_api_source_without_publisher():
    item = _item(url="https://apis.tianapi.com/science/index", publisher_url=None)
    result = grade_article_source(item, _source(type="api"), rules={})
    assert result.reason == "rule1:no-origin"


@pytest.mark.parametrize(
    ("level", "reason_prefix"), [("A", "rule2:"), ("B", "rule3:"), ("C", "rule4:")]
)
def test_rule2_3_4_hit_domain_rule(level, reason_prefix):
    item = _item(publisher_url="https://www.cast.org.cn")
    result = grade_article_source(
        item, _source(type="google_news"), rules={"cast.org.cn": level}
    )
    assert result.level == level
    assert result.reason == f"{reason_prefix}cast.org.cn"


def test_rule5_expected_domains_fallback():
    item = _item(resolved_url="https://news.fnal.gov/2026/11/neutrino/")
    source = _source(type="rss", level="A", expected_domains=("fnal.gov",))
    result = grade_article_source(item, source, rules={})
    assert (result.level, result.reason) == ("A", "rule5:fnal.gov")


def test_rule5_normalizes_expected_domains():
    item = _item(resolved_url="https://news.fnal.gov/x/")
    source = _source(type="rss", level="A", expected_domains=("https://www.fnal.gov/feed",))
    assert grade_article_source(item, source, rules={}).reason == "rule5:fnal.gov"


def test_rule5_not_applied_for_aggregator():
    """源级 A 的兜底只对 rss 生效；google_news 即使配了 expected_domains 也不兜底。"""
    item = _item(publisher_url="https://news.fnal.gov/x/")
    source = _source(type="google_news", level="A", expected_domains=("fnal.gov",))
    result = grade_article_source(item, source, rules={})
    assert result.level == "C"
    assert result.reason == "rule6:unknown:fnal.gov"
    assert result.unknown_domain == "fnal.gov"


def test_rule6_unknown_domain_recorded():
    item = _item(resolved_url="https://www.bhpioneer.com/community/neutrino-progress/")
    source = _source(type="rss", level="A", expected_domains=("fnal.gov",))
    result = grade_article_source(item, source, rules={})
    assert result.level == "C"
    assert result.reason == "rule6:unknown:bhpioneer.com"
    assert result.unknown_domain == "bhpioneer.com"


def test_suffix_match_is_exact_not_contains():
    """notnasa.gov 不能被 nasa.gov（或 .gov 后缀）命中。"""
    item = _item(resolved_url="https://notnasa.gov/a")
    result = grade_article_source(item, _source(type="rss", level="A", expected_domains=("nasa.gov",)), rules={"nasa.gov": "A"})
    assert result.level == "C"
    assert result.reason == "rule6:unknown:notnasa.gov"


def test_rss_uses_resolved_url_first_then_url():
    source = _source(type="rss", level="B")
    assert grade_article_source(
        _item(url="https://mirror.example.org/a", resolved_url="https://www.nasa.gov/a"),
        source,
        rules={"nasa.gov": "A"},
    ).reason == "rule2:nasa.gov"
    assert grade_article_source(
        _item(url="https://www.nasa.gov/a", resolved_url=None),
        source,
        rules={"nasa.gov": "A"},
    ).reason == "rule2:nasa.gov"
