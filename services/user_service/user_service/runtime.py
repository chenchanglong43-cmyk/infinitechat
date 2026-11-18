"""Construct process-specific UserService dependencies."""
# 用来组装数据库相关的底层资源，生成核心运行时对象
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
# AsyncEngine：数据库引擎，管连接池
# AsyncSession：单次数据库会话，用来写查询
# async_sessionmaker：会话工厂，用来生产会话

from user_service.config import UserServiceSettings
# 服务配置类，里面放mysql连接串等信息

from user_service.integrations.mysql import create_mysql_engine, create_session_factory
# create_mysql_engine：根据连接串创建异步数据库引擎
# create_session_factory：拿着引擎生成会话工厂

# 核心运行时，打包数据库相关资源
@dataclass(slots=True)
# slots=True，节省内存，不让随便新增属性
class CoreRuntime:
    engine: AsyncEngine
    # 数据库引擎，全局只创建一个
    session_factory: async_sessionmaker[AsyncSession]
    # 会话工厂，后面业务代码拿它生成数据库会话

    # 关闭资源的异步方法，服务停止的时候调用
    async def close(self) -> None:
        # 销毁引擎，关闭数据库连接池，释放连接
        await self.engine.dispose()

# 构建核心运行时，传入配置，产出装好数据库资源的CoreRuntime
def build_core_runtime(settings: UserServiceSettings) -> CoreRuntime:
    # 用配置里的mysql连接串，创建数据库引擎
    engine = create_mysql_engine(settings.mysql_dsn)
    # 用引擎生成会话工厂，打包成CoreRuntime返回
    return CoreRuntime(
        engine=engine,
        session_factory=create_session_factory(engine),
    )