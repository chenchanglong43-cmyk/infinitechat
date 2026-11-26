"""Kafka producer used by the realtime service."""

# 异步kafka生产者
from aiokafka import AIOKafkaProducer
# kafka主题枚举
from infinitechat.contracts.events import KafkaTopic
# 消息请求pydantic模型
from infinitechat.contracts.websocket import MessageRequest


# Realtime服务的Kafka生产者
class RealtimeProducer:
    def __init__(self, bootstrap: str, *, instance_id: str) -> None:
        # 初始化异步kafka生产者
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap,  # kafka地址
            client_id=f"realtime-{instance_id}",  # 客户端标识，区分不同实例
            transactional_id=f"realtime-{instance_id}",  # 事务id，开启kafka事务
            transaction_timeout_ms=30_000,  # 事务超时30秒
            request_timeout_ms=10_000,  # 单次发送请求超时10秒
        )

    # 启动生产者连接
    async def start(self) -> None:
        await self._producer.start()

    # 关闭生产者连接
    async def stop(self) -> None:
        await self._producer.stop()

    # 发布入站消息：同时发到 STORE 和 PUSH 两个主题，用Kafka事务保证两条要么都成功、要么都失败
    async def publish_inbound(self, message: MessageRequest) -> None:
        # pydantic模型转json字符串，再编码成bytes，kafka只传字节
        value = message.model_dump_json(by_alias=True).encode()
        # 关联id，用来追踪这条消息链路
        correlation = str(message.client_message_id or message.message_id or "unknown").encode()
        # 自定义消息头：链路追踪id、协议版本
        headers = [("correlation-id", correlation), ("schema-version", b"1")]
        # 开启kafka事务
        async with self._producer.transaction():
            # 发到 STORE 主题：负责持久化存消息（给消息存储服务消费）
            await self._producer.send_and_wait(
                KafkaTopic.STORE,
                value=value,
                headers=headers,
            )
            # 发到 PUSH 主题：负责分发推送（给推送服务消费，找到用户推ws）
            await self._producer.send_and_wait(
                KafkaTopic.PUSH,
                key=str(message.session_id).encode(),  # key用来分区，同一个会话的消息落到同一个分区，保序
                value=value,
                headers=headers,
            )

    # 只存库、不推送：给那些“只要留个记录”的消息用
    async def store_message(self, message: MessageRequest) -> None:
        # 开事务：这条消息要么发成功，要么当没发过
        async with self._producer.transaction():
            # 只往 STORE 主题丢，交给存储服务写进数据库
            await self._producer.send_and_wait(
                KafkaTopic.STORE,
                # 模型转 JSON 再编码成字节，Kafka 只认字节
                value=message.model_dump_json(by_alias=True).encode(),
                # 追踪号：优先用服务端消息ID，没有就写 unknown，方便日志串起来
                headers=[("correlation-id", str(message.message_id or "unknown").encode())],
            )


    # store_message 的典型场景（只存库、不推送）：
    # 1. 系统消息落档：比如“你已加入群聊”，只在聊天记录里留痕，不弹窗
    # 2. 历史补录/迁移：导入旧消息只写库，避免把旧消息重新推给用户
    # 3. 消息重放修复：推送失败的补偿只补存库，避免用户收到重复消息
    # 4. 审计/事件记录：需要进消息表追溯，但用户不可见
    # 5. 静默通知：只更新列表/红点，不弹窗、不响铃
    # 注意：用户离线不属于这里，正常聊天仍走 publish_inbound，
    #      离线由推送服务自己判断并跳过