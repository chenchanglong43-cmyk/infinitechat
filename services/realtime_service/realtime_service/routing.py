"""Message routing: validate, then hand messages to Kafka."""
# 消息路由模块：决定“一条消息该发给谁”，并把消息交给投递总线 / Kafka

from datetime import datetime  # 时间工具：把毫秒时间戳转成给人看的日期字符串
from typing import Protocol  # Protocol：只规定方法长相，不要求继承，方便替换实现和 mock
from zoneinfo import ZoneInfo  # 时区工具：用来指定上海时区

from infinitechat.contracts.enums import SessionType  # 会话类型枚举：区分私聊还是群聊
from infinitechat.contracts.websocket import (  # 导入 WebSocket 相关的数据模型
    MessageRequest,  # 客户端发进来的原始消息
    MessageResponse,  # 准备推给客户端的消息（时间已格式化）
    SystemNotification,  # 系统通知：只发给单个用户
)
from redis.asyncio import Redis  # 异步 Redis 客户端（当前作为依赖注入，方法里暂时没直接用）

from realtime_service.kafka import RealtimeProducer  # Kafka 生产者：负责把消息写进 STORE / PUSH

# 上海时区
SHANGHAI = ZoneInfo("Asia/Shanghai")  # 全局常量：后面格式化时间统一按上海时区


# 协议：规定“用户服务客户端”必须长什么样
class UserClient(Protocol):
    # 查一个会话里的全部成员 id
    async def members(self, session_id: int) -> list[int]: ...  # 传会话ID，返回成员ID列表


# 协议：规定“投递总线”必须长什么样
class DeliveryBus(Protocol):
    # 按用户 id 定向投递一段字符串，返回是否投递成功
    async def deliver(self, user_id: int, payload: str) -> bool: ...  # 真正送消息的动作在实现类里


# 消息路由：核心分发逻辑，决定这条消息要推给哪些用户
class MessageRouter:
    def __init__(
        self,
        *,  # 星号：后面参数必须按名字传，防止顺序传错
        delivery: DeliveryBus,  # 投递总线：负责把消息送到用户所在实例
        users: UserClient,  # 用户服务客户端：群聊时用来查成员
        producer: RealtimeProducer,  # Kafka 生产者：入站消息要先落 Kafka
        redis: Redis,  # Redis 客户端：预留依赖（当前方法里没用到）
    ) -> None:
        self._delivery = delivery  # 存下投递总线，route_message 里用
        self._users = users  # 存下用户服务客户端，群聊查成员用
        self._producer = producer  # 存下 Kafka 生产者，后续扩展用
        self._redis = redis  # 存下 Redis，预留

    # 路由一条聊天消息，返回成功推送给多少个在线用户
    async def route_message(self, message: MessageRequest) -> int:
        # 先把“请求模型”转成“推给前端的响应模型”（时间转可读字符串）
        response = self._response(message)
        # 判断是私聊还是群聊
        if message.session_type is SessionType.DIRECT:  # 私聊
            # 私聊：发送人和接收人都要收到；用 set 去重，receiver 可能为空，后面会过滤
            targets = {message.sender_id, message.receiver_id}
        else:  # 群聊
            # 群聊：去用户服务查这个会话的全部成员，set 去重
            targets = set(await self._users.members(message.session_id))

        delivered = 0  # 计数器：本轮成功送达多少个在线用户
        # 响应模型转 JSON 字符串；by_alias 用前端字段名，exclude_none 不输出空字段
        payload = response.model_dump_json(by_alias=True, exclude_none=True)
        # 挨个目标用户投递
        for target in targets:
            # 跳过空 ID；deliver 返回 True 表示用户在线且发送成功
            if target is not None and await self._delivery.deliver(target, payload):
                delivered += 1  # 成功一个，计数加一
        return delivered  # 返回本轮成功送达的在线人数

    # 系统通知：只发给指定用户
    async def route_notification(self, notification: SystemNotification) -> bool:
        delivered = await self._delivery.deliver(  # 调投递总线，直接发给收件人
            notification.receiver_id,  # 收件人用户ID
            notification.model_dump_json(by_alias=True, exclude_none=True),  # 通知转 JSON 字符串
        )
        return delivered  # 原样返回是否送达

    @staticmethod  # 静态方法：不需要访问 self，纯转换逻辑
    def _response(message: MessageRequest) -> MessageResponse:
        created = message.created_time  # 取出客户端带的创建时间
        # 如果是毫秒时间戳（int，或者纯数字字符串）
        if isinstance(created, int) or (isinstance(created, str) and created.isdigit()):
            formatted = datetime.fromtimestamp(  # 把时间戳转成 datetime 对象
                int(created) / 1000,  # 毫秒转秒
                SHANGHAI,  # 按上海时区
            ).strftime("%Y-%m-%d %H:%M:%S")  # 格式化成“年-月-日 时:分:秒”
        else:  # 不是时间戳
            formatted = str(created)  # 原样转成字符串
        # 推送消息必须带服务端 message_id，没有就报错，避免前端收到无法确认的消息
        if message.message_id is None:
            raise ValueError("push messageId is required")
        # 组装并返回前端要的响应模型
        return MessageResponse(
            session_id=message.session_id,  # 会话ID
            sender_id=message.sender_id,  # 发送人ID
            type=message.type,  # 消息类型：文本/图片/红包等
            session_type=message.session_type,  # 私聊 / 群聊
            created_time=formatted,  # 格式化后的时间字符串
            message_id=message.message_id,  # 服务端消息ID
            client_message_id=message.client_message_id,  # 客户端本地消息ID（用于去重/回执）
            body=message.body,  # 消息具体内容
        )
