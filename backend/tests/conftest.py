"""pytest 基础设施。

约定：
1. 只连 `TEST_DATABASE_URL`，绝不碰 dev 库；每个用例跑在**外层事务**里并回滚。
2. 全程不访问真实网络（需要 HTTP 的用例用 respx mock）。
3. 鉴权依赖统一被覆盖；`fetch` 层的模块级 Client 每个用例重置。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import require_admin
from app.core.config import settings
from app.core.db import Base, get_db
from app.models import Source, TaskRun
from app.services import fetch as fetch_module


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.TEST_DATABASE_URL, poolclass=NullPool, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def session(engine) -> Iterator[Session]:
    conn = engine.connect()
    trans = conn.begin()
    factory = sessionmaker(bind=conn, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        trans.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def _reset_http_client() -> Iterator[None]:
    fetch_module.reset_client()
    yield
    fetch_module.reset_client()


@pytest.fixture()
def client(session: Session) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_admin] = lambda: "test-admin"
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def make_source(session: Session):
    def _make(**kwargs) -> Source:
        defaults = dict(
            code=f"src_{abs(hash(kwargs.get('code', 'x'))) % 10**8}",
            name="测试源",
            type="rss",
            level="B",
            url="https://example.com/feed/",
            poll_interval_min=30,
            enabled=True,
            expected_domains=[],
            title_suffix_rules=[],
            extra_headers={},
            health_status="ok",
        )
        defaults.update(kwargs)
        source = Source(**defaults)
        session.add(source)
        session.flush()
        return source

    return _make


@pytest.fixture()
def make_task_run(session: Session):
    def _make(source: Source, *, status: str = "success", fetched: int = 1, **kwargs) -> TaskRun:
        run = TaskRun(
            task_type=kwargs.pop("task_type", "poll"),
            source_id=source.id,
            status=status,
            triggered_by="scheduler",
            started_at=kwargs.pop("started_at", datetime.now(timezone.utc)),
            finished_at=datetime.now(timezone.utc),
            stats_json=kwargs.pop("stats_json", {"fetched": fetched}),
        )
        session.add(run)
        session.flush()
        return run

    return _make
