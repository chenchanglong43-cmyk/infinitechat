"""Alembic environment: one migration chain for the whole database."""

import asyncio  # 异步编程
from logging.config import fileConfig
from typing import Any

from alembic import context

from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from red_packet_service.storage.models import Base as RedPacketBase
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from sqlalchemy.ext.asyncio import async_engine_from_config
from user_service.storage.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)  # 按配置文件设置日志

# 迁移要比较的模型清单: user 的模型, 红包的模型
target_metadata = [Base.metadata, RedPacketBase.metadata]

def database_url() -> str:
    value = read_env("MYSQL_DSN", "")
    if not value:
        # MYSQL_DSN is required for the MySQL migration job
        raise RuntimeError("MYSQL_DSN is required for the MySQL migration job")
    if not value.startswith("mysql+asyncmy://"):
        # MYSQL_DSN must use the mysql+asyncmy driver
        raise RuntimeError("MYSQL_DSN must use the mysql+asyncmy driver")
    return value

def run_migrations_offline() -> None:
    context.configure(  # 做配置
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():  # 开启迁移事务
        context.run_migrations()  # 执行迁移

def do_run_migrations(connection: Connection) -> None:
    # 做配置
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():  # 开启迁移事务
        context.run_migrations()  # 执行迁移

async def run_async_migrations() -> None:
    # section(字典(字符串 -> 任意类型)): 调用 get_section
    section: dict[str, Any] = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:  # 建立连接 connection, 用完自动收尾
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())  # 启动事件循环跑这个协程: 调用 run_async_migrations

if context.is_offline_mode():
    run_migrations_offline()  # 离线模式执行迁移(只生成 SQL)
else:
    run_migrations_online()  # 连上数据库执行迁移