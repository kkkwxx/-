"""模型统一出口。Alembic autogenerate 依赖这里 import 全部表。"""

from __future__ import annotations

from app.core.db import Base
from app.models.ai import AiRun
from app.models.api_provider import AiProviderConfig
from app.models.audit import ReviewAction
from app.models.keyword import KeywordRule
from app.models.pipeline import Article, RawItem
from app.models.policy import RiskPolicyConfig
from app.models.publish import EventCluster, PublishedArticle, PushRecord
from app.models.schedule import PipelineScheduleConfig
from app.models.source import Source, SourceDomainRule, UnknownDomain
from app.models.task import TaskRun

__all__ = [
    "Base",
    "AiProviderConfig",
    "AiRun",
    "Article",
    "EventCluster",
    "KeywordRule",
    "PipelineScheduleConfig",
    "PublishedArticle",
    "PushRecord",
    "RawItem",
    "ReviewAction",
    "RiskPolicyConfig",
    "Source",
    "SourceDomainRule",
    "TaskRun",
    "UnknownDomain",
]
