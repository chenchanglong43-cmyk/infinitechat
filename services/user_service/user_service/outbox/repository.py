"""Lease-based outbox persistence for at-least-once Kafka publishing."""
# 基于租约机制的发件箱，保证消息至少投递一次到Kafka

from collections.abc import Sequence
# 序列类型注解，用来接收列表这类可迭代对象
from dataclasses import dataclass
# 用来定义数据类，省手写__init__
from datetime import timedelta
# 时间差，用来设置租约过期时长
from typing import Any
# 任意类型注解

from sqlalchemy import and_, or_, select, update
# sqlalchemy查询语法：条件、查询、更新语句
from sqlalchemy.ext.asyncio import AsyncSession
# 异步数据库会话

from user_service.storage.models import OutboxEventModel
# 发件箱数据库模型

from user_service.storage.repositories import database_now
# 获取数据库当前时间的工具函数

# 发件箱事件数据类
@dataclass(frozen=True, slots=True)
# frozen=True：实例创建后不能改；slots=True：节省内存，速度更快
class OutboxEvent:
    event_id: str  # 事件唯一ID
    topic: str  # 要投递到的Kafka主题
    event_key: str | None  # Kafka消息key，用来分区，可以为空
    payload: dict[str, Any]  # 消息主体数据
    headers: dict[str, Any]  # 消息头，放链路追踪等信息
    attempts: int  # 已经尝试发送的次数

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

    # 认领一批待发送事件，加租约锁，防止多个worker抢同一条消息
    async def claim(self, *, limit: int, lease_seconds: int = 30) -> tuple[OutboxEvent, ...]:
        # 拿数据库当前时间，避免本地机器时间和数据库时间对不上
        now = database_now()
        # 组装可以被认领的条件，满足下面任意一种情况就行
        claimable = or_(
            and_(
                # 状态是待发送或者等待重试
                OutboxEventModel.status.in_(("pending", "retry_wait")),
                # 已经到了允许发送的时间点
                OutboxEventModel.available_at <= now,
            ),
            and_(
                # 之前认领过，标记为发送中，但是租约超时卡住了
                OutboxEventModel.status == "publishing",
                # 租约时间已经过期，可以重新抢这条消息
                OutboxEventModel.available_at <= now,
            ),
        )
        # 查询符合认领条件的数据，加行锁，跳过已经被其他worker锁住的行
        rows: Sequence[OutboxEventModel] = (
            await self.session.scalars(
                select(OutboxEventModel)
                .where(claimable)  # 用上前面拼好的认领规则
                .order_by(OutboxEventModel.created_time)  # 按创建时间排序，保证先进先出
                .with_for_update(skip_locked=True)  # 加行锁，别的worker不会读到这条
                .limit(limit)  # 最多一次取出limit条
            )
        ).all()
        # 计算租约到期时间，在这个时间之前别的worker不能抢
        lease_until = now + timedelta(seconds=lease_seconds)
        # 列表，用来存放转换成业务层的事件对象
        events: list[OutboxEvent] = []
        # 遍历数据库查出来的每一条记录
        for row in rows:
            # 状态改成正在发送，告诉别的worker这条我拿了
            row.status = "publishing"
            # 发送尝试次数加一
            row.attempts += 1
            # 更新租约过期时间，锁住这条消息一段时间
            row.available_at = lease_until
            # 数据库模型转成业务事件对象，填充各个字段
            events.append(
                OutboxEvent(
                    event_id=row.event_id,       # 事件唯一id
                    topic=row.topic,             # kafka主题名
                    event_key=row.event_key,     # kafka消息key
                    payload=dict(row.payload),   # 消息体，转成普通字典
                    headers=dict(row.headers or {}), # 消息头，为空就给空字典
                    attempts=row.attempts,       # 当前这是第几次尝试发送
                )
            )
        # 转成元组返回，外面不能随便增删
        return tuple(events)

    # 标记事件发送成功
    async def mark_published(self, event_id: str) -> None:
        await self.session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.event_id == event_id,  # 根据事件ID匹配这条记录
                OutboxEventModel.status == "publishing",  # 只更新处于发送中的数据
            )
            .values(
                status="published",  # 修改状态为已发布
                published_time=database_now(),  # 记录发送成功的时间，用数据库时间
                last_error=None,  # 清空之前存的错误信息
            )
        )

    # 标记事件发送失败
    async def mark_failed(
            self,
            event_id: str,
            *,  # 星号：后面所有参数强制只能关键字传参，防止传错顺序
            attempts: int,
            max_attempts: int,
            reason: str,
    ) -> None:
        # 指数退避计算重试延迟，上限300秒，避免疯狂重试打崩服务
        """
           1. `min(attempts, 8)`
            最多取到 8，就算 attempts=100，也按 8 算。
            目的：不让指数无限暴涨。
            2. `2 ** x` 就是 2 的 x 次方

            - attempts=1 → 2¹ = 2 秒
            - attempts=2 → 2² = 4 秒
            - attempts=3 → 8 秒
            - attempts=4 → 16 秒
            - attempts=5 → 32 秒
            - attempts=6 → 64 秒
            - attempts=7 → 128 秒
            - attempts=8 → 256 秒

            >
            > attempts ≥8 之后，永远是 256
        """
        delay_seconds = min(300, 2 ** min(attempts, 8))
        terminal = attempts >= max_attempts  # 判断：重试次数是否已经到头
        await self.session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.event_id == event_id,  # 按事件id定位
                OutboxEventModel.status == "publishing",  # 只处理发送中的记录
            )
            .values(
                # 次数耗尽丢死信，否则改成等待重试状态
                status="dead_letter" if terminal else "retry_wait",
                available_at=database_now() + timedelta(seconds=delay_seconds),  # 设置下一次允许重试的时间点
                last_error=reason[:1000],  # 保存报错原因，最多存1000字符，防止字段溢出
            )
        )