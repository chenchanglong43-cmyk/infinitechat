"""连接归属演示.

用户从实例 A 换到实例 B: A 上的旧连接晚一步断开, 也不会把 B 刚写下的归属删掉;
B 上断开时才真正清掉归属, 并记下下线时间.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

# 异步协程
import asyncio
# json序列化，解析redis里的归属信息
import json

# 结构化日志
import structlog

# 读取环境变量、.env配置
from infinitechat.foundation.settings import read_env
# 日志初始化工具
from infinitechat.foundation.telemetry import configure_logging
# 导入连接实体、连接管理器
from realtime_service.connections import Connection, ConnectionManager
# 异步redis客户端
from redis.asyncio import Redis

# 拿到日志对象
logger = structlog.get_logger(__name__)

# 测试用固定用户id
USER_ID = 9_000_009_101


# 模拟WS对象，不用真实网络，只记录发出去的消息
class FakeSocket:
    """替身 WebSocket: 只记下发给它的文本, 不做真实的网络收发."""

    def __init__(self) -> None:
        self.sent: list[str] = []  # 存所有推送过来的消息

    async def send_text(self, data: str) -> None:
        """记下这条文本."""
        self.sent.append(data)


# 工具函数，读取redis，看用户当前归属哪个实例
async def owner(redis: Redis) -> str | None:
    """读出这个用户的连接现在归哪个实例."""
    raw = await redis.get(ConnectionManager.connection_key(USER_ID))
    return json.loads(raw)["instanceId"] if raw else None


async def main() -> None:
    """同一个用户先连 A, 再连 B, 然后 A, B 先后断开."""
    # 连接redis，2号库，自动解码字符串
    redis = Redis.from_url(read_env("REDIS_URL"), db=2, decode_responses=True, protocol=2)
    # 实例A
    node_a = ConnectionManager(
        redis,
        instance_id="realtime-a",
        ttl_seconds=45,
        send_timeout_seconds=5.0,
    )
    # 实例B
    node_b = ConnectionManager(
        redis,
        instance_id="realtime-b",
        ttl_seconds=45,
        send_timeout_seconds=5.0,
    )
    try:
        # 先清理旧的离线标记
        await redis.delete(f"user:offline:{USER_ID}")
        # 用户在A建立第一条连接
        old = Connection(USER_ID, "conn-old", FakeSocket())
        await node_a.add(old)
        logger.info("连上 A", owner=await owner(redis))

        # 1. 用户切换到B，redis归属直接覆盖成B
        new = Connection(USER_ID, "conn-new", FakeSocket())
        await node_b.add(new)
        logger.info("换到 B", owner=await owner(redis))

        # 2. 晚一点A这边旧连接才断开，Lua脚本校验发现instance/connId对不上，不会删B的归属
        removed = await node_a.remove(old)
        can_send = await node_a.send(USER_ID, "hi")
        logger.info(
            "A 的旧连接断开",
            removed=removed,
            owner=await owner(redis),
            a_can_send=can_send,
        )

        # 3. B这边连接正常断开，Lua校验通过，清除归属，写入离线标记
        await node_b.remove(new)
        offline = bool(await redis.exists(f"user:offline:{USER_ID}"))
        logger.info("B 也断开", owner=await owner(redis), offline_recorded=offline)
    finally:
        # 关闭redis连接，防止泄漏
        await redis.aclose()


if __name__ == "__main__":
    configure_logging(json_output=False)  # 控制台日志不打json，方便看
    asyncio.run(main())  # 启动异步事件循环跑测试