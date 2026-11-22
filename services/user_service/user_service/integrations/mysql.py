"""MySQL engine and session lifecycle."""
# MySQL 异步引擎、会话工厂的创建逻辑，统一管理数据库连接生命周期

from sqlalchemy.ext.asyncio import (
    AsyncEngine,  # 异步数据库引擎，负责连接池管理
    AsyncSession,  # 异步会话，用来执行单条事务、CRUD
    async_sessionmaker,  # 会话工厂，每次拿新的 AsyncSession
    create_async_engine,  # 创建异步引擎的顶层函数
)

# 创建MySQL异步引擎
def create_mysql_engine(dsn: str) -> AsyncEngine:
    # 校验协议，必须使用 asyncmy 驱动，不允许别的驱动
    if not dsn.startswith("mysql+asyncmy://"):
        raise ValueError("UserService MYSQL_DSN must use mysql+asyncmy")
    return create_async_engine(
        dsn,
        pool_pre_ping=True,  # 拿连接之前先ping一下，剔除已经断了的连接
        pool_recycle=1800,  # 连接最长存活1800秒（30分钟），到点自动回收重建
    )

# 创建会话工厂
def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False：提交事务后，对象不自动过期，提交后还能读取属性
    return async_sessionmaker(engine, expire_on_commit=False)