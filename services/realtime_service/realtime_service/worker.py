"""Push worker: consume push events and deliver them to users."""
# 推送 Worker：消费 Kafka 里的聊天推送事件和系统通知，交给路由器投递给用户

import asyncio  # 异步编程：用任务组并发跑多个消费者
import json  # JSON 处理：系统通知先解析成 JSON，再转成模型
from collections.abc import Awaitable, Callable  # 类型注解：可等待对象、可调用对象

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore[import-untyped]
# Kafka 异步消费者；TopicPartition 表示“某个主题的某个分区”

from aiokafka.structs import OffsetAndMetadata  # type: ignore[import-untyped]
# 偏移元数据：手动提交“下一条从哪个位置开始消费”
from infinitechat.contracts.events import KafkaTopic  # Kafka 主题枚举
from infinitechat.contracts.types import JsonValue  # JSON 值类型别名

from infinitechat.contracts.websocket import MessageRequest, parse_system_notification
# 聊天消息模型；系统通知解析函数

from realtime_service.config import RealtimeSettings  # 实时服务配置
from realtime_service.routing import MessageRouter  # 消息路由器

# 消费指定 Kafka 主题
async def consume(
    settings: RealtimeSettings,  # 服务配置：Kafka 地址、实例 ID
    *,  # 星号后面的参数只能按名字传
    topic: str,  # 要消费的主题
    handler: Callable[[bytes], Awaitable[object]],  # 收到消息后的处理函数，入参是原始字节
) -> None:
    # 消费组拼上实例 ID：每个实例一个独立消费组，各自都会收到全量消息
    group = f"realtime-{topic}-{settings.instance_id}"
    consumer = AIOKafkaConsumer(
        topic,  # 订阅的主题
        bootstrap_servers=settings.kafka_bootstrap_servers,  # Kafka 集群地址
        group_id=group,  # 消费组 ID
        enable_auto_commit=False,  # 关闭自动提交，业务成功后再手动提交
        auto_offset_reset="earliest",  # 新消费组没有位点时，从最早的消息开始读
    )
    await consumer.start()  # 启动消费者，连接 Kafka
    try:  # 下面这段可能出错，出错也走 finally 收尾
        while True:  # 一直循环，直到 break、return 或抛异常
            record = await consumer.getone()  # 阻塞等待，拿一条消息
            await handler(record.value)  # 交给业务处理（传原始字节）
            partition = TopicPartition(record.topic, record.partition)  # 当前消息所在的分区
            # 处理成功才提交 offset：下次从下一条开始，保证“至少一次”
            await consumer.commit({partition: OffsetAndMetadata(record.offset + 1, "")})
    finally:  # 不管成功还是出错，最后都执行这里
        await consumer.stop()  # 关闭消费者，释放连接

# 运行推送与通知路由 Worker
async def run_worker(settings: RealtimeSettings, router: MessageRouter) -> None:
    # 处理聊天消息：字节 → MessageRequest → 交给路由器分发
    async def message(payload: bytes) -> object:
        return await router.route_message(MessageRequest.model_validate_json(payload))

    # 处理系统通知：字节 → JSON → SystemNotification → 交给路由器
    async def notification(payload: bytes) -> object:
        decoded: JsonValue = json.loads(payload)  # 先解析成通用 JSON 值
        return await router.route_notification(parse_system_notification(decoded))

    async with asyncio.TaskGroup() as tasks:  # 任务组：任一任务出错，其余任务一起取消
        tasks.create_task(consume(settings, topic=KafkaTopic.PUSH, handler=message))  # 消费聊天推送
        tasks.create_task(  # 消费系统通知
            consume(
                settings,
                topic=KafkaTopic.SYSTEM_NOTIFICATION,  # 系统通知主题
                handler=notification,  # 收到后走通知路由
            )
        )
    