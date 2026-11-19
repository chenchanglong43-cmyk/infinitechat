"""批量查询演示.

一次查 50 个用户的昵称和头像, 只发出 1 条 SQL; 一次超过 100 个直接拒绝.
前置条件: 需本地 MySQL, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
from typing import Any

import structlog  # 结构化日志

from infinitechat.foundation.telemetry import configure_logging
from sqlalchemy import event

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from user_service.config import UserServiceSettings
from user_service.errors import BusinessError

from user_service.integrations.minio import AvatarStorage

from user_service.integrations.mysql import create_mysql_engine, create_session_factory
from user_service.storage.models import UserModel

from user_service.storage.repositories import UserRepository
from user_service.users import UserProfileService

logger = structlog.get_logger(__name__)

FIRST_ID = 9_000_001_000
COUNT = 50
MISSING_ID = 9_000_001_999
# 演示账号不能登录, 密码列放一个不可能匹配的占位值
DISABLED_LOGIN = "!demo!"  # 常量 禁用login: 演示账号不能登录

# 准备 50 个用户, 已经有了就跳过
async def seed(sessions: async_sessionmaker[AsyncSession]) -> None:
    """准备 50 个用户, 已经有了就跳过."""
    # 开数据库会话 session, 再开启事务(出错自动回滚)
    async with sessions() as session, session.begin():
        repository = UserRepository(session)
        existing = await repository.list_by_ids([FIRST_ID + i for i in range(COUNT)])
        for user_id in range(FIRST_ID, FIRST_ID + COUNT):
            if user_id not in existing:
                repository.add(  # 加进去
                    UserModel(
                        user_id=user_id,
                        email=f"batch{user_id}@example.com",
                        password=DISABLED_LOGIN,
                        nickname=f"用户{user_id % 1000}",
                    )
                )

# 数一数批量查询到底发了几条 SQL
async def main() -> None:
    """数一数批量查询到底发了几条 SQL, 再试一次超过上限."""
    settings = UserServiceSettings()
    engine = create_mysql_engine(settings.mysql_dsn)
    sessions = create_session_factory(engine)
    profiles = UserProfileService(
        session_factory=sessions,
        avatar_storage=AvatarStorage(settings),
        allowed_avatar_hosts=frozenset(),
    )
    statements: list[str] = []  # statements(字符串列表): 空列表

    # 引擎每执行一条 SQL 都记下来
    def record(*args: Any) -> None:
        """引擎每执行一条 SQL 都记下来, 第三个参数就是 SQL 文本."""
        statements.append(str(args[2]))  # 把 转成字符串 追加到statements

    try:  # 下面这段可能出错, 出错交给 except 处理
        await seed(sessions)
        # 1. 查 50 个存在的用户, 外加 1 个不存在的
        # 每执行一条 SQL 之前记一笔, 用来数查询次数
        event.listen(engine.sync_engine, "before_cursor_execute", record)
        infos = await profiles.batch_infos([FIRST_ID + i for i in range(COUNT)] + [MISSING_ID])
        event.remove(engine.sync_engine, "before_cursor_execute", record)  # 移除连接并标记离线
        logger.info(  # 打一条 info 日志
            "批量查询",
            asked=COUNT + 1,
            found=len(infos),
            sql_statements=len(statements),
            uses_in=" IN (" in statements[0].upper(),
        )
        # 2. 一次超过 100 个, 还没查库就拒绝
        try:  # 下面这段可能出错, 出错交给 except 处理
            await profiles.batch_infos(list(range(1, 102)))
        except BusinessError as error:
            # 打一条 warning 日志
            logger.warning("数量超限", error_code=int(error.code), message=error.message)
    finally:  # 不管成功还是出错, 最后都执行这里
        await engine.dispose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程