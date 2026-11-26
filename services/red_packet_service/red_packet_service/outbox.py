"""Red packet outbox: write events in the business transaction."""
# 红包服务的 Outbox（发件箱）：业务写库时，顺手把“要发的消息”也写进同一张表
# 这样业务数据落库成功，消息就不会丢；之后再由后台发布器把 pending 的事件发去 Kafka

from dataclasses import dataclass  # 数据类：用来定义轻量的数据载体
from datetime import datetime  # 取当前时间，作为“可发布时间”
from typing import Any  # 消息体/headers 都是任意 JSON

from infinitechat.foundation.context import current_request_context  # 取当前请求上下文（关联ID等）
from sqlalchemy.ext.asyncio import AsyncSession  # SQLAlchemy 异步会话

from red_packet_service.storage.models import OutboxEvent  # outbox_event 表的 ORM 映射

# 已认领、等待发布的事件：发布器从数据库捞出来之后，用这个对象在内存里传递
@dataclass(frozen=True, slots=True)  # 数据类：自动生成构造方法；frozen 不可改，slots 省内存
class ClaimedEvent:
    event_id: str  # 事件唯一ID，日志追踪和消费端去重都能用
    topic: str  # 要发去哪个 Kafka topic
    key: str | None  # 分区键，可为空；同一个业务的消息尽量落同一分区
    payload: dict[str, Any]  # 消息体（JSON）
    headers: dict[str, Any]  # 消息头（JSON）
    attempts: int  # 已经尝试发过几次，用来限制重试次数

# 写入 Outbox 事件：注意这里是 session.add，不是直接 commit
# 它必须和业务写库共用同一个事务：业务提交它就一起提交，业务回滚它也跟着回滚
def add_event(
    session: AsyncSession,
    *,  # 星号后面的参数只能按名字传
    event_id: str,
    aggregate_id: int,
    event_type: str,
    topic: str,
    key: str | None,
    payload: dict[str, Any],
) -> None:
    # 取当前请求上下文；如果没有（比如后台任务里调用），就是 None
    context = current_request_context()
    session.add(  # 放进会话，等业务 flush/commit 时一起写库
        OutboxEvent(
            event_id=event_id,
            aggregate_type="red_packet",  # 来源类型固定为红包服务
            aggregate_id=str(aggregate_id),  # 来源ID转成字符串，表里对应的是字符串列
            event_type=event_type,  # 事件类型，比如 red_packet.created
            topic=topic,
            event_key=key,  # 对应表里的 event_key（分区键）
            payload=payload,
            headers={
                "event-id": event_id,  # 事件ID：方便日志追踪，也可供消费端去重
                "schema-version": "1",  # 消息格式版本，将来改结构时用来兼容老消费者
                # 有上下文就带上 correlation-id，方便跨服务串起同一条链路；没有就不加这个键
                **({"correlation-id": context.correlation_id} if context is not None else {}),
            },
            status="pending",  # 初始状态：待发布
            attempts=0,  # 还没发过
            available_at=datetime.now(),  # 现在就可以发（失败重试时会把时间往后推）
        )
    )