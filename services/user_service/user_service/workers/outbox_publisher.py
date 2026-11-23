"""Publish claimed outbox rows and preserve retry state on failure."""
# 把认领出来的发件箱消息投递出去，失败时保存重试状态

from typing import Protocol
# 定义协议，鸭子类型，只要类有send方法就能当生产者，不用强继承

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 异步数据库会话、会话工厂，用来创建db连接

from user_service.outbox import OutboxEvent, OutboxRepository
# 发件箱事件实体类、发件箱仓储类，封装数据库操作

# 事件生产者协议，约定生产者必须实现send方法
class EventProducer(Protocol):
    # 发送事件的接口规范
    async def send(
        self,
        *,  # 强制后面所有参数用关键字传参，防止传参顺序搞错
        topic: str,
        key: str | None,
        payload: dict[str, object],
        headers: dict[str, object],
    ) -> None: ...

# 发件箱发布器：拉取消息、调用生产者发消息、更新消息状态
class OutboxPublisher:
    # 初始化，外部注入依赖，解耦
    def __init__(
        self,
        *,  # 强制关键字传参
        session_factory: async_sessionmaker[AsyncSession],
        producer: EventProducer,
        batch_size: int = 100,  # 一轮最多捞多少条待发消息
        max_attempts: int = 10,  # 单条消息最大重试次数
    ) -> None:
        self._sessions = session_factory  # 存会话工厂，后面随时开db会话
        self._producer = producer  # kafka生产者实例
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    # 跑一轮发布任务，返回这一轮一共处理了多少条消息
    async def run_once(self) -> int:
        # 新开会话+开启事务，认领一批消息，认领的时候会上锁，防止别的进程抢到
        async with self._sessions() as session, session.begin():
            events = await OutboxRepository(session).claim(limit=self._batch_size)
        # 遍历每条消息，挨个发送
        for event in events:
            await self._publish(event)
        return len(events)

    # 内部方法：单独发一条事件消息
    async def _publish(self, event: OutboxEvent) -> None:
        try:
            # 调用生产者，把消息发到kafka
            await self._producer.send(
                topic=event.topic,        # 消息投递到哪个topic
                key=event.event_key,      # kafka分区key，用来决定分区
                payload=event.payload,    # 消息主体数据
                headers=event.headers,    # 消息头，放追踪id之类附加信息
            )
        except Exception as error:
            # 发送报错了，去数据库标记这条消息失败，留给后面重试
            async with self._sessions() as session, session.begin():
                await OutboxRepository(session).mark_failed(
                    event.event_id,                # 消息唯一id
                    attempts=event.attempts,       # 当前已经重试次数
                    max_attempts=self._max_attempts,# 允许的最大重试次数
                    reason=type(error).__name__,   # 只存异常类名，简单记录失败原因
                )
            return
        # 走到这里说明发送成功，更新数据库标记这条消息已经发布完成
        async with self._sessions() as session, session.begin():
            await OutboxRepository(session).mark_published(event.event_id)