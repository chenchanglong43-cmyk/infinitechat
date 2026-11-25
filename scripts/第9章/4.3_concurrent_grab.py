"""金额池原子领取演示.

10 份金额放进 Redis 列表, 100 个人同时抢: 恰好 10 个人抢到, 加起来正好是总额, 只有一个人
拿到"抢完了"的标记; 抢到的人再抢一次, 拿到的还是原来那一份; 金额池只会初始化一次.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import random  # 随机数
import time  # 时间

import structlog  # 结构化日志

from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.foundation.telemetry import configure_logging
from red_packet_service.allocation import allocate_random

from red_packet_service.config import redis_url_for_database
from red_packet_service.redis_store import RedPacketRedis
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

# 一个 10 元 10 份的金额池
async def main() -> None:
    """一个 10 元 10 份的金额池, 100 个人同时抢."""
    redis = Redis.from_url(
        redis_url_for_database(read_env("REDIS_URL"), 0),
        decode_responses=True,
        protocol=2,
    )
    store = RedPacketRedis(redis, ttl_seconds=3600)
    packet_id = time.time_ns()
    amounts = allocate_random(1000, 10, rng=random.Random(1))  # noqa: S311 - 固定种子便于复现
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 1. 金额池只初始化一次
        first = await store.initialize(packet_id, amounts)
        second = await store.initialize(packet_id, amounts)
        logger.info("初始化金额池", first=first, second=second)  # 打一条 info 日志
        # 2. 100 个人同时抢
        users = range(1, 101)
        results = await asyncio.gather(*(store.claim(packet_id, user) for user in users))
        winners = {
            user: result.amount  # 用户
            for user, result in zip(users, results, strict=True)
            if result.code == 0
        }
        logger.info(  # 打一条 info 日志
            "100 人同时抢",
            winners=len(winners),
            empty=sum(result.code == -2 for result in results),
            total=sum(amount or 0 for amount in winners.values()),
            completed_flags=sum(result.completed for result in results),
        )
        # 3. 抢到的人再抢一次: 返回 -1 和他原来那一份
        lucky = next(iter(winners))
        again = await store.claim(packet_id, lucky)
        # 打一条 info 日志
        logger.info("再抢一次", result=again.code, same_amount=again.amount == winners[lucky])
        # 4. 领取记录和抢到的人一一对应
        records = await store.records(packet_id)
        logger.info("领取记录", count=len(records), matches=records == winners)  # 打一条 info 日志
    finally:  # 不管成功还是出错, 最后都执行这里
        await store.cleanup(packet_id)
        await redis.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 一个 10 元 10 份的金额池