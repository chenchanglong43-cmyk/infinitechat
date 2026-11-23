"""发布失败退避演示.

发不出去的事件不会丢: 次数加一, 推迟重试, 记下失败原因; 到了次数上限就进死信, 不再重试.
前置条件: 需本地 MySQL, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步库，用来控制等待、并发
import uuid  # 生成随机标识
from typing import Any  # 任意类型注解

import structlog  # 结构化日志，方便看输出

from infinitechat.foundation.telemetry import configure_logging
# 日志初始化工具

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 异步数据库会话相关类型
from user_service.config import UserServiceSettings
# 用户服务配置，自动读取.env

from user_service.integrations.mysql import create_mysql_engine, create_session_factory
# 创建mysql引擎和会话工厂
from user_service.outbox import OutboxRepository
# 发件箱仓储类

from user_service.storage.models import OutboxEventModel
# 发件箱数据库模型

from user_service.storage.repositories import database_now
# 获取数据库当前时间

from user_service.workers.outbox_publisher import OutboxPublisher
# 发件箱发布器核心类

logger = structlog.get_logger(__name__)
# 获取日志实例

async def clear_backlog(sessions: async_sessionmaker[AsyncSession]) -> None:
    """先把库里已有的待发事件认领走(30 秒后它们会重新可认领), 后面只看本脚本写的事件."""
    # 开启会话+事务，认领存量旧消息，避免干扰本次测试
    async with sessions() as session, session.begin():
        await OutboxRepository(session).claim(limit=10_000)

# 按给定的 Topic 各写一条演示事件
async def write_events(sessions: async_sessionmaker[AsyncSession], topics: list[str]) -> list[str]:
    """按给定的 Topic 各写一条演示事件, 返回事件 ID."""
    run = uuid.uuid4().hex[:8]  # 生成随机串，标记本次测试批次
    event_ids = [f"demo-{run}-{index}" for index in range(len(topics))]
    # 批量构造事件id
    async with sessions() as session, session.begin():
        outbox = OutboxRepository(session)
        for event_id, topic in zip(event_ids, topics, strict=True):
            outbox.add(  # 插入一条测试事件到发件箱表
                event_id=event_id,
                aggregate_type="demo",
                aggregate_id=event_id,
                event_type="Demo",
                topic=topic,
                event_key=event_id,
                payload={"eventId": event_id},
            )
    return event_ids

# 读回一条事件的当前状态
async def load(sessions: async_sessionmaker[AsyncSession], event_id: str) -> OutboxEventModel:
    """读回一条事件的当前状态."""
    async with sessions() as session:  # 打开会话，用完自动关闭
        row = await session.get(OutboxEventModel, event_id)
        assert row is not None  # 查不到直接报错，保证数据存在
        return row

BROKEN_TOPIC = "demo-broken-topic"
# 标记这个topic是坏的，用来模拟发送失败

# 替身生产者: 不连 Kafka
class FlakyProducer:
    """替身生产者: 不连 Kafka, 发往 BROKEN_TOPIC 的一律失败, 其余记下来."""

    def __init__(self) -> None:
        """还没发出任何事件."""
        self.sent: list[str] = []  # 存已经成功发送的事件key

    # 模拟一次发送: 坏 Topic 抛连接错误
    async def send(
        self,
        *,  # 后面参数必须关键字传参
        topic: str,
        key: str | None,
        payload: dict[str, Any],
        headers: dict[str, Any],
    ) -> None:
        """模拟一次发送: 坏 Topic 抛连接错误, 好 Topic 记下事件 ID."""
        if topic == BROKEN_TOPIC:
            raise ConnectionError("kafka unreachable")  # 模拟kafka连不上，发送失败
        self.sent.append(str(key))  # 正常topic，记录这条消息发送成功

# 一条能发, 一条注定发不出去
async def main() -> None:
    """一条能发, 一条注定发不出去, 连跑几轮发布器, 每轮看两条事件的状态."""
    settings = UserServiceSettings()
    engine = create_mysql_engine(settings.mysql_dsn)  # 创建mysql连接引擎
    sessions = create_session_factory(engine)  # 创建会话工厂
    producer = FlakyProducer()  # 用假生产者，不用真实kafka
    publisher = OutboxPublisher(session_factory=sessions, producer=producer, max_attempts=2)
    # 发布器，最多重试2次
    try:
        await clear_backlog(sessions)
        good, bad = await write_events(sessions, ["demo-topic", BROKEN_TOPIC])
        # 写入两条测试消息：一条正常、一条会失败
        await asyncio.sleep(1)
        # 数据库时间是秒级，等一下保证消息可以被认领
        # 1. 第一轮：能发的发出去，发不出去的进入重试等待，记下原因
        await publisher.run_once()
        row = await load(sessions, bad)
        wait = round((row.available_at - database_now()).total_seconds())
        logger.info(
            "发送成功",
            status=(await load(sessions, good)).status,
            sent=good in producer.sent,
        )
        logger.warning(
            "发送失败",
            status=row.status,
            attempts=row.attempts,
            retry_in_s=wait,
            error=row.last_error,
        )
        # 2. 马上再跑：还没到重试时间，这条事件原样不动
        await publisher.run_once()
        row = await load(sessions, bad)
        logger.info("立刻重跑", status=row.status, attempts=row.attempts)
        # 3. 到了重试时间：第二次还是失败，次数到上限，进死信
        await asyncio.sleep(3)
        await publisher.run_once()
        row = await load(sessions, bad)
        logger.warning("第二次失败", status=row.status, attempts=row.attempts)
        # 4. 再等一轮：死信不会再被认领，次数不再增加
        await asyncio.sleep(3)
        await publisher.run_once()
        row = await load(sessions, bad)
        logger.info("死信之后", status=row.status, attempts=row.attempts)
    finally:
        await engine.dispose()  # 最后释放数据库连接

if __name__ == "__main__":
    # 直接运行这个文件才执行
    configure_logging(json_output=False)  # 日志打印成人类可读格式，不是json
    asyncio.run(main())  # 启动异步入口，跑退避重试演示