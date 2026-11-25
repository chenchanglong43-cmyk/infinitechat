"""Cross-node delivery bus and local subscription."""

# json序列化、解析消息包
import json

# 异步redis客户端，用来做发布订阅
from redis.asyncio import Redis

# 导入本机连接管理器，用来本地推送消息
from realtime_service.connections import ConnectionManager


# Redis跨实例投递总线，用Redis Pub/Sub实现多实例消息分发
class RedisDeliveryBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    # 给用户发消息：先查归属，再publish到用户所在实例的订阅通道
    async def deliver(self, user_id: int, payload: str) -> bool:
        # 读取redis里用户当前归属记录
        ownership = await self._redis.get(f"realtime:connection:{user_id}")
        # 查不到归属代表用户离线，直接返回false
        if not ownership:
            return False
        # 解析拿到用户当前在线的实例id
        instance_id = json.loads(str(ownership))["instanceId"]
        # 往这个实例专属的channel发消息包
        await self._redis.publish(
            self.channel(instance_id),
            json.dumps(
                {"userId": user_id, "payload": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        return True

    # 拼接实例对应的订阅通道名
    @staticmethod
    def channel(instance_id: str) -> str:
        return f"realtime:deliver:{instance_id}"


# 后台协程：持续订阅本机的投递通道，收到消息就调用manager推给ws
async def run_local_delivery(
    redis: Redis,
    manager: ConnectionManager,
) -> None:
    # 开启redis pubsub订阅连接，上下文结束自动关闭
    async with redis.pubsub() as pubsub:
        # 订阅当前这个realtime实例专属的channel
        await pubsub.subscribe(RedisDeliveryBus.channel(manager.instance_id))
        # 循环监听订阅消息，异步迭代
        async for event in pubsub.listen():
            # 只处理message类型，跳过订阅成功、心跳等别的事件
            if event["type"] != "message":
                continue
            # 解析拿到消息体
            envelope = json.loads(str(event["data"]))
            # 调用连接管理器，本机推送
            await manager.send(int(envelope["userId"]), str(envelope["payload"]))