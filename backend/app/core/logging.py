"""日志：控制台 + JSON（python-json-logger）。"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from app.core.config import settings

_configured = False


def setup_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.APP_ENV == "dev":
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL.upper())
    # 第三方库噪音
    for noisy in ("httpx", "httpcore", "trafilatura", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
