"""Alembic 运行时环境。

连接串不写在 alembic.ini，统一从 app.core.config.settings 注入，
保证迁移、应用、测试三处用同一个口径（技术栈 §5.3）。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.pool import NullPool

from app.core.config import settings

# import app.models 的目的：把全部 7 张表注册进 Base.metadata
import app.models  # noqa: F401
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 由 settings 注入真实连接串（alembic.ini 里留空）
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata

# pg_trgm 的 GIN 索引由 0001 迁移手工创建（技术栈 §5.3），models 里没有声明它。
# 不排除的话，每次 autogenerate / check 都会生成一条「删除该索引」。
MANAGED_OUTSIDE_MODELS = {"ix_article_title_trgm"}


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """autogenerate 的对象过滤器：跳过迁移手工管理、models 里不存在的对象。"""
    if type_ == "index" and name in MANAGED_OUTSIDE_MODELS:
        return False
    return True


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连库执行迁移。迁移进程不常驻，用 NullPool。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
