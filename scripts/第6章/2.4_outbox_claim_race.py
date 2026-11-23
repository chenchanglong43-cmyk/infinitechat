"""outbox 认领演示.

两个发布进程同时认领, 每条事件只落到一个手里; 租约期内谁也拿不走;
发布进程崩了也不怕, 租约一过事件重新可认领, 次数加一.
前置条件: 需本地 MySQL, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步库，用来跑并发任务
import uuid  # 生成唯一标识

import structlog  # 结构化日志库，打印好看的日志

from infinitechat.foundation.telemetry import configure_logging
# 日志链路初始化工具

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 异步会话、会话工厂类型注解
from user_service.config import UserServiceSettings
# 用户服务配置类，自动读环境变量

from user_service.integrations.mysql import create_mysql_engine, create_session_factory
# 创建mysql引擎、创建会话工厂
from user_service.outbox import OutboxRepository
# 发件箱仓储类，核心认领、写入逻辑

from user_service.storage.models import OutboxEventModel
# 发件箱数据库表模型

logger = structlog.get_logger(__name__)
# 获取当前模块的日志对象

async def clear_backlog(sessions: async_sessionmaker[AsyncSession]) -> None:
    """先把库里已有的待发事件认领走(30 秒后它们会重新可认领), 后面只看本脚本写的事件."""
    # 开启会话 + 事务，出错自动回滚
    async with sessions() as session, session.begin():
        await OutboxRepository(session).claim(limit=10_000)
        # 一次性认领旧数据，把存量事件全部占住，避免干扰本次测试

# 按给定的 Topic 各写一条演示事件
async def write_events(sessions: async_sessionmaker[AsyncSession], topics: list[str]) -> list[str]:
    """按给定的 Topic 各写一条演示事件, 返回事件 ID."""
    run = uuid.uuid4().hex[:8]  # 生成一段随机串，区分本次测试批次
    event_ids = [f"demo-{run}-{index}" for index in range(len(topics))]
    # 批量构造事件id，格式：demo-随机串-序号
    # 开会话和事务，写入事件
    async with sessions() as session, session.begin():
        outbox = OutboxRepository(session)
        for event_id, topic in zip(event_ids, topics, strict=True):
            outbox.add(  # 往发件箱插入一条事件记录
                event_id=event_id,
                aggregate_type="demo",
                aggregate_id=event_id,
                event_type="Demo",
                topic=topic,
                event_key=event_id,
                payload={"eventId": event_id},
            )
    return event_ids  # 返回这批新事件id，后面用来判断

# 读回一条事件的当前状态
async def load(sessions: async_sessionmaker[AsyncSession], event_id: str) -> OutboxEventModel:
    """读回一条事件的当前状态."""
    async with sessions() as session:  # 打开数据库会话，用完自动关闭
        row = await session.get(OutboxEventModel, event_id)
        assert row is not None  # 断言必须查到这条记录，找不到直接抛异常
        return row

# 开一个独立的事务认领一批
async def claim_ids(
    sessions: async_sessionmaker[AsyncSession],
    limit: int,
    mine: set[str],
) -> list[str]:
    """开一个独立的事务认领一批, 只返回本脚本写的那些事件."""
    # 开启会话+事务执行认领
    async with sessions() as session, session.begin():
        events = await OutboxRepository(session).claim(limit=limit, lease_seconds=1)
    # 过滤，只保留本次测试自己新增的事件
    return [event.event_id for event in events if event.event_id in mine]

# 写 6 条事件, 两个认领者抢
async def main() -> None:
    """写 6 条事件, 两个认领者抢, 再看租约到期后的样子."""
    settings = UserServiceSettings()  # 加载配置
    engine = create_mysql_engine(settings.mysql_dsn)  # 创建mysql连接引擎
    sessions = create_session_factory(engine)  # 生成会话工厂
    try:
        await clear_backlog(sessions)  # 先清存量，防止干扰测试
        mine = set(await write_events(sessions, ["demo-topic"] * 6))  # 写入6条测试事件
        # 数据库时间是秒级，等一下，确保事件到可认领时间
        await asyncio.sleep(1)
        # 1. 两个认领任务并发同时去抢，验证互斥锁
        first, second = await asyncio.gather(
            claim_ids(sessions, 10, mine),
            claim_ids(sessions, 10, mine),
        )
        overlap = len(set(first) & set(second))  # 看有没有抢到同一条（正常应该是0）
        logger.info("同时认领", sizes=sorted([len(first), len(second)]), overlap=overlap)
        # 2. 租约还没过期，再认领一次，应该一条都拿不到
        logger.info("租约期内", claimed=len(await claim_ids(sessions, 10, mine)))
        # 3. 模拟worker宕机，不标记成功/失败，等待租约超时
        await asyncio.sleep(2.5)
        again = await claim_ids(sessions, 10, mine)
        attempts = sorted({(await load(sessions, event_id)).attempts for event_id in again})
        logger.info("租约过期后", claimed=len(again), attempts=attempts)
        # 4. 收尾，把这批事件标记成功，避免残留影响下次测试
        async with sessions() as session, session.begin():
            for event_id in again:
                await OutboxRepository(session).mark_published(event_id)
    finally:
        await engine.dispose()  # 不管成功失败，最后释放数据库连接

if __name__ == "__main__":
    # 直接运行这个文件才会执行
    configure_logging(json_output=False)  # 日志不输出json，方便人看
    asyncio.run(main())  # 启动异步入口，跑整个测试流程