"""Lease-based outbox persistence for at-least-once Kafka publishing."""
# 基于租约机制的发件箱，用来保证Kafka消息至少投递一次
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from user_service.storage.models import OutboxEventModel
from user_service.storage.repositories import database_now

# 发件箱事件实体，对应数据库里的outbox表一条记录
@dataclass(frozen=True, slots=True)
# frozen=True：这个实例一旦创建，字段值不能修改，防止意外篡改数据
# slots=True：不用默认__dict__，节省内存、访问速度更快
class OutboxEvent:
    event_id: str              # 事件唯一ID，用来做幂等，防止重复发消息
    topic: str                 # Kafka主题，消息要发到哪个topic
    event_key: str | None      # Kafka消息key，用来分区，没有可以传None
    payload: dict[str, Any]    # 消息主体内容，业务数据放这里
    headers: dict[str, Any]    # Kafka消息头，可以放追踪ID、来源标记这类
    attempts: int              # 投递重试次数，失败后+1，超过阈值就不再重试


# 发件箱仓储，专门操作outbox发件箱表，用来实现事务发件箱模式，保证Kafka消息可靠投递
class OutboxRepository:
    # 初始化方法
    # 把数据库会话存到实例里面，后面所有操作都复用这个session
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # 新增一条待发送的消息事件
    # 前面带*，代表所有参数都必须关键字传参，防止参数顺序传错
    def add(
        self,
        *,
        event_id: str,          # 事件唯一id，用来做幂等判断
        aggregate_type: str,    # 聚合根类型，比如user、friend，标记这个事件属于哪个业务模块
        aggregate_id: str,      # 聚合根id，比如用户id，对应这条业务数据的id
        event_type: str,        # 事件类型，比如user_created、friend_added，区分是什么事件
        topic: str,             # Kafka主题，消息要发到哪个topic
        event_key: str | None,  # Kafka消息key，用来控制分区，没有就传None
        payload: dict[str, Any],# 消息主体，业务数据放这里
        headers: dict[str, Any] | None = None, # Kafka消息头，链路追踪id之类的，可选
    ) -> None:
        # 组装数据库模型对象，加到session缓存里
        # 注意：这里只是进缓存，等上层事务commit之后才真正写入数据库
        self.session.add(
            OutboxEventModel(
                event_id=event_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                topic=topic,
                event_key=event_key,
                payload=payload,
                headers=headers or {}, # 如果headers是None，就换成空字典，避免报错
                status="pending",      # 状态：pending=待投递，等待后台任务去发Kafka
                attempts=0,            # 投递尝试次数，刚创建是0次，失败重试才会累加
                available_at=database_now(), # 这条事件什么时候可以被投递，现在就可以发
            )
        )

    # 幂等新增：如果这个event_id不存在才写入，已经存在就不重复添加
    # 返回值：True=成功新增；False=这条事件已经存在，没有新增
    async def add_if_absent(self, **event: Any) -> bool:
        # 从传入参数里拿出event_id，转成字符串
        event_id = str(event["event_id"])
        # 根据主键event_id去数据库查这条记录
        if await self.session.get(OutboxEventModel, event_id) is not None:
            # 查到了，说明已经存在，直接返回False，不重复写入
            return False
        # 不存在，调用上面add方法加到session缓存
        self.add(**event)
        return True

