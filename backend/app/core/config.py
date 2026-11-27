"""全局配置。所有数值以 技术栈 §8 与 04-采集 §15 为准，不在代码里另立默认值。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/ -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 运行环境 ----
    APP_ENV: str = "dev"
    APP_TIMEZONE: str = "Asia/Shanghai"
    LOG_LEVEL: str = "INFO"

    # ---- 数据库 ----
    DATABASE_URL: str = (
        "postgresql+psycopg://mystery:change-me@localhost:5433/mystery_news"
    )
    TEST_DATABASE_URL: str = (
        "postgresql+psycopg://mystery:change-me@localhost:5433/mystery_news_test"
    )

    # ---- 认证（单管理员） ----
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD_HASH: str = ""
    JWT_SECRET: str = "please-change-this-to-a-long-random-string"
    JWT_EXPIRE_MINUTES: int = 720
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- 采集（技术栈 §5.5） ----
    CRAWL_USER_AGENT: str = "MysteryNewsDemo/1.0 (+mailto:contact@example.com)"
    CRAWL_MAX_ATTEMPTS: int = 5
    CRAWL_TIMEOUT_SECONDS: float = 25.0
    CRAWL_SOURCE_CONCURRENCY: int = 4
    MAX_AGE_DAYS: int = 3
    MAX_RESPONSE_BYTES: int = 5_242_880
    HOST_MIN_INTERVAL_SEC: float = 1.0
    GOOGLE_NEWS_MIN_INTERVAL_SEC: float = 2.0
    FULL_TEXT_BATCH_SIZE: int = 20

    # 探活参数必须独立于采集（03 §8.4 V1）
    VALIDATE_TIMEOUT_SECONDS: float = 8.0
    VALIDATE_MAX_ATTEMPTS: int = 1

    # ---- 代理：可选（04 §7.6） ----
    HTTP_PROXY: str | None = None
    HTTPS_PROXY: str | None = None

    # ---- AI（百炼 OpenAI 兼容接口；B-07 拍板：qwen-flash 分类 + qwen-plus 生成） ----
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL_CLASSIFY: str = "qwen-flash"
    LLM_MODEL_GENERATE: str = "qwen-plus"
    LLM_MODEL_HARD: str = "qwen3.8-max"
    LLM_RESPONSE_FORMAT: str = "json_object"
    LLM_DAILY_TOKEN_BUDGET: int = 200_000

    # ---- 预处理与事件判定（05 §11） ----
    PREPROCESS_BATCH_SIZE: int = 50        # 每轮处理条数
    PREPROCESS_CONCURRENCY: int = 4        # AI 并发，受 LLM_DAILY_TOKEN_BUDGET 约束
    EVENT_CONFIDENCE_MIN: float = 0.7      # 进 candidate 的置信度门槛（05 §6.5）
    NOT_EVENT_CONFIDENCE_MIN: float = 0.8  # 直接判 not_event 的置信度门槛（05 §6.5）
    PROMPT_VERSION: str = "v2"             # 写进 ai_run.prompt_version，改 prompt 必须升版本

    # ---- 去重与时效门禁（06） ----
    DEDUP_TRGM_THRESHOLD: float = 0.65     # pg_trgm 即时去重相似度阈值
    DEDUP_WINDOW_HOURS: int = 72           # 即时去重检索窗口（小时）
    FRESHNESS_HOURS: int = 48              # 推送时效门禁（需求 §6/§8：48 小时内）
    FUTURE_EVENT_MAX_DAYS: int = 7         # 未来事件（如小行星飞掠）放行上限（天）

    # ---- AI 内容加工（07） ----
    GENERATE_BATCH_SIZE: int = 20          # 每轮加工条数
    GENERATE_CONCURRENCY: int = 2          # 文案生成并发
    GENERATE_PROMPT_VERSION: str = "gen-v1"
    # B-05 标题去标点字数口径：建议 10—18，放行 7—18，硬上限 20
    TITLE_MIN_CHARS: int = 7
    TITLE_MAX_CHARS: int = 20

    # ---- 其它 ----
    REPLAY_MODE: bool = False
    RAW_HTML_MAX_DB_BYTES: int = 1_048_576

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.CORS_ORIGINS.split(",") if item.strip()]

    @property
    def proxy(self) -> str | None:
        return self.HTTPS_PROXY or self.HTTP_PROXY or None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
