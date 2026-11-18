"""Construct process-specific UserService dependencies."""

from dataclasses import dataclass

from infinitechat.foundation.ids import SnowflakeGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from user_service.config import UserServiceSettings

from user_service.integrations.mysql import create_mysql_engine, create_session_factory

from user_service.integrations.redis import UserRedis

# 核心运行时
@dataclass(slots=True)  # 数据类: 自动生成构造函数等方法, 省内存
# 核心运行时
class CoreRuntime:
    engine: AsyncEngine  # 数据库引擎：异步数据库引擎
    # 会话工厂：异步会话工厂
    session_factory: async_sessionmaker[AsyncSession]
    cache: UserRedis  # 缓存：用户Redis
    ids: SnowflakeGenerator  # ID：雪花生成器

    # 关闭资源
    async def close(self) -> None:
        await self.cache.aclose()
        await self.engine.dispose()

# 构建核心运行时
def build_core_runtime(settings: UserServiceSettings) -> CoreRuntime:
    engine = create_mysql_engine(settings.mysql_dsn)
    return CoreRuntime(
        engine=engine,
        session_factory=create_session_factory(engine),
        # cache(缓存): 按地址建客户端
        cache=UserRedis.from_url(settings.redis_url, database=settings.redis_db),
        ids=SnowflakeGenerator(
            worker_id=settings.snowflake_worker_id,
            data_center_id=settings.snowflake_data_center_id,
        ),
    )