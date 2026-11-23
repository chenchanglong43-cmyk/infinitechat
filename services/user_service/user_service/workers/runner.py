"""User worker process: outbox publisher plus two friend-request consumers."""
# 用户服务Worker进程：包含发件箱发布轮询 + 两路好友申请Kafka消费者

import asyncio  # 导入异步IO库
from collections.abc import Awaitable, Callable  # 导入异步可等待对象、可调用对象类型注解

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore[import-untyped]
# 导入aiokafka异步消费者、主题分区对象，忽略类型检查提示

from aiokafka.structs import OffsetAndMetadata  # type: ignore[import-untyped]
# 导入偏移元数据结构体，用于手动提交消费位点
from infinitechat.contracts.events import (
    ConsumerGroup,
    FriendRequestCreated,
    FriendRequestExpired,
    KafkaTopic,
)
# 导入契约定义：消费组枚举、好友申请创建事件、好友申请过期事件、Kafka主题枚举
from pydantic import BaseModel  # 导入pydantic基础模型，用于事件校验

from user_service.config import UserServiceSettings  # 导入用户服务配置类
from user_service.integrations.kafka import UserKafkaProducer  # 导入封装好的Kafka生产者

from user_service.workers.friend_requests import FriendRequestHandlers  # 导入好友申请事件处理器
from user_service.workers.outbox_publisher import OutboxPublisher  # 导入发件箱发布器

# 泛型消费函数：统一消费Kafka消息，反序列化事件并执行handler，手动提交offset
async def _consume[EventT: BaseModel](
    *,  # 标记后面所有参数必须使用关键字传参
    settings: UserServiceSettings,  # 服务配置实例
    topic: KafkaTopic,  # 要消费的Kafka主题
    group: ConsumerGroup,  # 消费组标识
    event_type: type[EventT],  # 事件模型类型（泛型）
    handler: Callable[[EventT], Awaitable[object]],  # 业务处理函数，入参事件，返回协程
) -> None:
    # 实例化Kafka异步消费者
    consumer = AIOKafkaConsumer(
        topic,  # 订阅的主题
        bootstrap_servers=settings.kafka_bootstrap_servers,  # kafka集群地址
        group_id=group,  # 当前消费组id
        client_id=f"{settings.kafka_client_id}-{group}",  # 客户端id，区分不同消费组
        enable_auto_commit=False,  # 关闭自动提交offset，业务成功后手动提交
        auto_offset_reset="earliest",  # 没有位点时从最早消息开始消费
    )
    await consumer.start()  # 启动消费者，连接kafka
    try:  # try块捕获消费循环内异常
        while True:  # 无限循环持续拉取消息
            record = await consumer.getone()  # 阻塞等待，拿到一条kafka消息记录
            event = event_type.model_validate_json(record.value)  # 把消息二进制json反序列化为事件对象
            await handler(event)  # 调用业务handler处理这条事件
            partition = TopicPartition(record.topic, record.partition)  # 构造当前消息对应的分区对象
            # 提交offset：标记下一次从offset+1开始消费
            await consumer.commit({partition: OffsetAndMetadata(record.offset + 1, "")})
    finally:  # 无论正常退出还是报错，都会执行
        await consumer.stop()  # 关闭消费者，释放连接

# 轮询发件箱：不停执行outbox发布，没消息就休眠
async def _publish_outbox(
    publisher: OutboxPublisher,  # 发件箱发布器实例
    *,  # 标记后面参数必须关键字传参
    poll_seconds: float,  # 空轮询时的休眠秒数
) -> None:
    while True:  # 无限循环持续轮询发件箱
        published = await publisher.run_once()  # 执行一轮发布，返回本次成功发布数量
        if published == 0:  # 本轮没有事件可以发布
            await asyncio.sleep(poll_seconds)  # 休眠指定时长，避免空转cpu

# 启动用户Worker主入口，并发运行3个任务
async def run_user_worker(
    *,  # 标记后面参数必须关键字传参
    settings: UserServiceSettings,  # 服务配置
    handlers: FriendRequestHandlers,  # 好友申请事件处理器实例
    publisher: OutboxPublisher,  # 发件箱发布器实例
    producer: UserKafkaProducer,  # kafka生产者实例
) -> None:
    await producer.start()  # 启动kafka生产者，建立连接
    try:  # try块捕获任务组内异常
        # 创建任务组，组内任意任务异常，全部任务自动取消
        async with asyncio.TaskGroup() as tasks:
            # 任务1：轮询outbox，把发件箱事件推送到Kafka
            tasks.create_task(_publish_outbox(publisher, poll_seconds=settings.outbox_poll_seconds))
            # 任务2：消费好友申请创建事件，调用handler.enqueue写入Redis过期队列
            tasks.create_task(
              _consume(
                    settings=settings,
                    topic=KafkaTopic.FRIEND_REQUEST_CREATION,              # 订阅的主题
                    group=ConsumerGroup.FRIEND_REQUEST_ENQUEUER,           # 消费组
                    event_type=FriendRequestCreated,                       # 消息反序列化成什么模型
                    handler=handlers.enqueue,                              # 拿到事件后干什么
                )
            )
            # 任务3：消费好友申请过期事件，调用handler.expire更新数据库状态为过期
            tasks.create_task(
                _consume(
                    settings=settings,
                    topic=KafkaTopic.FRIEND_REQUEST_EXPIRATION,
                    group=ConsumerGroup.FRIEND_REQUEST_EXECUTOR,
                    event_type=FriendRequestExpired,
                    handler=handlers.expire,
                )
            )
    finally:  # 无论正常退出还是报错，都会执行
        await producer.stop()  # 关闭kafka生产者，释放连接