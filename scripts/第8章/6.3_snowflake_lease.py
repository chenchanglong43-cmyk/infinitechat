"""雪花节点租约演示.

同一组数据中心号和机器号, 同一时刻只有一个进程拿得到; 别人续不了约, 也释放不了;
持有者释放以后别的进程才能接手; 后台续约让租约一直有效.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程

import structlog  # 结构化日志

from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.foundation.ids import SnowflakeNodeConflictError, SnowflakeNodeLease

from infinitechat.foundation.telemetry import configure_logging
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

# 为演示节点 31-31 建一个租约
def lease(redis: Redis, label: str, ttl_seconds: int = 30) -> SnowflakeNodeLease:
    """为演示节点 31-31 建一个租约, 演示进程都不会用到这个节点号."""
    return SnowflakeNodeLease(
        redis,
        data_center_id=31,
        worker_id=31,
        owner_label=label,
        ttl_seconds=ttl_seconds,
    )

# 两个进程抢同一个节点号
async def main() -> None:
    """两个进程抢同一个节点号, 再看续约和释放."""
    redis = Redis.from_url(read_env("REDIS_URL"), db=2, decode_responses=True, protocol=2)
    # 拆成 第一
    first, second = lease(redis, "demo:api-1"), lease(redis, "demo:api-2")
    try:  # 下面这段可能出错, 出错交给 except 处理
        await first.acquire()
        # 1. 第二个进程抢同一个节点号
        try:  # 下面这段可能出错, 出错交给 except 处理
            await second.acquire()
        except SnowflakeNodeConflictError as error:
            # 打一条 warning 日志
            logger.warning("第二个进程", error=type(error).__name__, held=second.held)
        # 2. 续约只认持有者; 不是持有者释放也不起作用
        # 打一条 info 日志
        logger.info("续约", by_owner=await first.refresh(), by_other=await second.refresh())
        await second.release()
        # 打一条 info 日志
        logger.info("别人释放之后", still_leased=bool(await redis.exists(first.key)))
        # 3. 持有者释放以后, 第二个进程就能接手
        await first.release()
        await second.acquire()
        logger.info("持有者释放之后", second_held=second.held)  # 打一条 info 日志
        await second.release()
        # 4. 后台续约: 租约只有 2 秒, 等 3 秒依然有效
        keeper = lease(redis, "demo:api-3", ttl_seconds=2)
        await keeper.acquire()
        stop = asyncio.Event()
        task = asyncio.create_task(keeper.maintain(stop))
        await asyncio.sleep(3)
        # 打一条 info 日志
        logger.info("后台续约", held=keeper.held, ttl_left=await redis.ttl(keeper.key) > 0)
        stop.set()  # 把停止设成
        await task
        await keeper.release()
    finally:  # 不管成功还是出错, 最后都执行这里
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 两个进程抢同一个节点号