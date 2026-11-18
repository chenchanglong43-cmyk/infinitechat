"""Alembic environment: one migration chain for the whole database."""

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
# 导入我们所有ORM模型的基类，Alembic靠它对比表结构差异
from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from user_service.storage.models import Base

config = context.config
# 读取alembic.ini，初始化日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 告诉Alembic要监控的模型元数据
target_metadata = [Base.metadata]

def database_url() -> str:
    # 从环境变量读取数据库连接串，迁移脚本不硬编码DSN
    value = read_env("MYSQL_DSN", "")
    if not value:
        raise RuntimeError("MYSQL_DSN is required for the MySQL migration job")
    # 强制校验驱动，和我们业务引擎保持一致 mysql+asyncmy
    if not value.startswith("mysql+asyncmy://"):
        raise RuntimeError("MYSQL_DSN must use the mysql+asyncmy driver")
    return value

def run_migrations_offline() -> None:
    """离线模式：只生成SQL脚本，不连接数据库执行"""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,  # 开启字段类型对比，类型变更也会生成迁移
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    """在同步连接上执行迁移逻辑，给run_sync调用"""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    """异步模式主流程：创建异步引擎，拿到连接，同步执行迁移"""
    section: dict[str, Any] = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    # NullPool：迁移脚本一次性连接，不需要连接池
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    """在线模式：真正连接数据库，执行迁移"""
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()