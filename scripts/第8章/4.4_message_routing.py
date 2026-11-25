"""消息路由演示.

单聊发给收发双方; 群聊按成员列表逐个投递, 不在线的成员跳过; 系统通知推给接收人,
通知只推送、不落库; 没有消息 ID 的消息拒绝路由.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import time  # 时间
import uuid

import structlog  # 结构化日志

from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.contracts.enums import MessageType, SessionType
from infinitechat.contracts.websocket import (
    FriendApplicationNotification,
    FriendApplicationNotificationBody,
    MessageBody,
    MessageRequest,
)

from infinitechat.foundation.telemetry import configure_logging

from realtime_service.connections import Connection, ConnectionManager

from realtime_service.delivery import RedisDeliveryBus, run_local_delivery
from realtime_service.routing import MessageRouter
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

# 拆成 alice, bob, carol: (9000009301, 9000009302, 9000009303)
ALICE, BOB, CAROL = 9_000_009_301, 9_000_009_302, 9_000_009_303

# 替身 WebSocket: 只记下发给它的文本
class FakeSocket:
    """替身 WebSocket: 只记下发给它的文本, 不做真实的网络收发."""

    def __init__(self) -> None:  # 构造方法: 创建对象时把依赖存起来
        """还没收到任何文本."""
        self.sent: list[str] = []  # 已发(字符串列表): 空列表

    # 记下这条文本
    async def send_text(self, data: str) -> None:
        """记下这条文本."""
        self.sent.append(data)  # 把 数据 追加到已发

# 替身 user 服务: 群成员固定是阿甲
class StubUsers:
    """替身 user 服务: 群成员固定是阿甲, 阿乙, 阿丙三个人."""

    # 不管问哪个群, 都返回这三个人
    async def members(self, session_id: int) -> list[int]:
        """不管问哪个群, 都返回这三个人."""
        return [ALICE, BOB, CAROL]

# 替身 Kafka 生产者
class StubProducer:
    """替身 Kafka 生产者: 消息路由用不上它, 只占个位."""

# 拼一条阿甲发出的文本消息
def message(
    session_type: SessionType,
    receiver: int | None,
    message_id: int | None,
) -> MessageRequest:
    """拼一条阿甲发出的文本消息."""
    return MessageRequest(
        session_id=1,
        receiver_id=receiver,
        sender_id=ALICE,
        type=MessageType.TEXT,
        session_type=session_type,
        created_time=time.time_ns() // 1_000_000,
        message_id=message_id,
        body=MessageBody(content="大家好"),
    )

# 阿甲和阿乙在线, 阿丙不在线
async def main() -> None:
    """阿甲和阿乙在线, 阿丙不在线, 发一条单聊, 一条群聊, 一条通知."""
    redis = Redis.from_url(read_env("REDIS_URL"), db=2, decode_responses=True, protocol=2)
    run = uuid.uuid4().hex[:6]  # 运行: 生成随机 UUID的hex[切片]
    node = ConnectionManager(
        redis,
        instance_id=f"node-{run}",
        ttl_seconds=45,
        send_timeout_seconds=5.0,
    )
    sockets = {ALICE: FakeSocket(), BOB: FakeSocket()}  # 连接: 2 个键的字典
    connections = [
        Connection(user_id, f"conn-{user_id}", socket)  # 每一项取 创建WebSocket 连接
        for user_id, socket in sockets.items()
    ]
    for connection in connections:
        await node.add(connection)
    await redis.delete(ConnectionManager.connection_key(CAROL))
    listener = asyncio.create_task(run_local_delivery(redis, node))
    producer = StubProducer()  # 创建替身 Kafka 生产者: 消息路由用不上它, 只占个位
    router = MessageRouter(
        delivery=RedisDeliveryBus(redis),
        users=StubUsers(),
        producer=producer,
        redis=redis,
    )
    try:  # 下面这段可能出错, 出错交给 except 处理
        await asyncio.sleep(0.3)
        # 1. 单聊发给收发双方, 群聊发给在线的成员
        direct = await router.route_message(message(SessionType.DIRECT, BOB, 1))
        group = await router.route_message(message(SessionType.GROUP, None, 2))
        await asyncio.sleep(0.3)
        # 已领: 2 个键的字典
        received = {"alice": len(sockets[ALICE].sent), "bob": len(sockets[BOB].sent)}
        # 打一条 info 日志
        logger.info("单聊与群聊", direct_delivered=direct, group_delivered=group, received=received)
        # 2. 同一条系统通知路由两次: 每次都推给接收人, 不再落库
        notice = FriendApplicationNotification(
            message_id=f"msg_demo_{run}",
            sender_id=ALICE,
            receiver_id=BOB,
            timestamp=time.time_ns() // 1_000_000,
            # body(消息体): 创建好友申请通知体
            body=FriendApplicationNotificationBody(nickname="阿甲", avatar=None, msg="加个好友"),
        )
        first = await router.route_notification(notice)
        second = await router.route_notification(notice)
        logger.info("通知路由两次", first=first, second=second)  # 打一条 info 日志
        # 3. 没有消息 ID 的消息
        try:  # 下面这段可能出错, 出错交给 except 处理
            await router.route_message(message(SessionType.GROUP, None, None))
        except ValueError as error:
            logger.warning("没有消息 ID", error=type(error).__name__)  # 打一条 warning 日志
    finally:  # 不管成功还是出错, 最后都执行这里
        listener.cancel()  # 取消监听者
        await asyncio.gather(listener, return_exceptions=True)
        for connection in connections:
            await node.remove(connection)
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 阿甲和阿乙在线, 阿丙不在线