"""跨节点投递演示.

阿甲连在实例 A, 阿乙连在实例 B. 投递总线按 Redis 里的归属, 把消息发到阿乙所在实例的通道,
再由那个实例推给阿乙; 不在线的用户直接返回投递失败.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import uuid

import structlog  # 结构化日志

from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.foundation.telemetry import configure_logging

from realtime_service.connections import Connection, ConnectionManager

from realtime_service.delivery import RedisDeliveryBus, run_local_delivery
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

# 拆成 alice, bob, nobody: (9000009201, 9000009202, 9000009299)
ALICE, BOB, NOBODY = 9_000_009_201, 9_000_009_202, 9_000_009_299

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

# 两个实例各自订阅自己的投递通道
async def main() -> None:
    """两个实例各自订阅自己的投递通道, 再从外面给阿乙和一个不在线的人发消息."""
    redis = Redis.from_url(read_env("REDIS_URL"), db=2, decode_responses=True, protocol=2)
    run = uuid.uuid4().hex[:6]  # 运行: 生成随机 UUID的hex[切片]
    node_a = ConnectionManager(
        redis,
        instance_id=f"node-a-{run}",
        ttl_seconds=45,
        send_timeout_seconds=5.0,
    )
    node_b = ConnectionManager(
        redis,
        instance_id=f"node-b-{run}",
        ttl_seconds=45,
        send_timeout_seconds=5.0,
    )
    # 拆成 alice连接
    alice_socket, bob_socket = FakeSocket(), FakeSocket()
    alice = Connection(ALICE, "conn-alice", alice_socket)
    bob = Connection(BOB, "conn-bob", bob_socket)
    await node_a.add(alice)
    await node_b.add(bob)
    await redis.delete(ConnectionManager.connection_key(NOBODY))
    # 监听地址: (节点a, 节点b)里每个节点算出的 后台起一个任务
    listeners = [asyncio.create_task(run_local_delivery(redis, node)) for node in (node_a, node_b)]
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 1. 等两个实例都订阅好自己的投递通道
        await asyncio.sleep(0.3)
        bus = RedisDeliveryBus(redis)
        to_bob = await bus.deliver(BOB, '{"content":"阿乙你好"}')
        to_nobody = await bus.deliver(NOBODY, '{"content":"有人吗"}')
        # 2. 等实例 B 从通道里收到, 推给阿乙
        await asyncio.sleep(0.3)
        logger.info("投递结果", to_bob=to_bob, to_nobody=to_nobody)  # 打一条 info 日志
        logger.info("各自收到", bob=bob_socket.sent, alice=alice_socket.sent)  # 打一条 info 日志
    finally:  # 不管成功还是出错, 最后都执行这里
        for task in listeners:
            task.cancel()  # 取消任务
        await asyncio.gather(*listeners, return_exceptions=True)
        await node_a.remove(alice)
        await node_b.remove(bob)
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程