"""好友申请到期队列演示.

到期的申请只会被认领一次; 认领了却迟迟不确认, 租约一过就回到待办;
确认之后不再出现; 调度锁同一时刻只有一个人拿得到, 也只有持有者能释放.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步库，跑并发任务
import time  # 获取时间戳

import structlog  # 结构化日志

from infinitechat.foundation.settings import read_env  # 读取环境变量，加载.env
from infinitechat.foundation.telemetry import configure_logging  # 日志初始化

from user_service.integrations.redis import UserRedis  # 上面封装好的Redis类

logger = structlog.get_logger(__name__)

DAY_MS = 86_400_000  # 一天的毫秒数
LEASE_MS = 30_000  # 认领租约时长30秒

# 以 now_ms 为当前时间认领到期项
async def claim(cache: UserRedis, now_ms: int, mine: set[int]) -> list[int]:
    """以 now_ms 为当前时间认领到期项, 只返回本脚本登记的那些."""
    claims = await cache.claim_due_friend_requests(
        now_ms=now_ms,
        limit=100,
        lease_until_ms=now_ms + LEASE_MS,  # 租约到期时间
    )
    # 过滤，只保留本次脚本新增的申请ID
    return [apply_id for apply_id, _ in claims if apply_id in mine]

# 登记三条申请, 两条已到期
async def main() -> None:
    """登记三条申请, 两条已到期, 一条明天才到期."""
    cache = UserRedis.from_url(read_env("REDIS_URL"))  # 初始化Redis实例
    now = time.time_ns() // 1_000_000  # 转成毫秒时间戳
    # 构造测试ID：两条已经到期，一条要等到明天
    due, later = [now * 10 + 1, now * 10 + 2], now * 10 + 3
    mine = {*due, later}  # 存本次测试的所有申请ID

    try:
        # 写入三条好友申请过期记录
        await cache.enqueue_friend_expiration(due[0], now - 2_000)
        await cache.enqueue_friend_expiration(due[1], now - 1_000)
        await cache.enqueue_friend_expiration(later, now + DAY_MS)

        # 1. 两个调度器并发认领，验证互斥，不会抢到同一条
        first, second = await asyncio.gather(claim(cache, now, mine), claim(cache, now, mine))
        logger.info(
            "同时认领",
            total=len(first) + len(second),
            overlap=len(set(first) & set(second)),
            later_claimed=later in first + second,
        )

        # 2. 认领后不确认：租约没到期不会放回；租约超时自动放回待办，可以再次认领
        early = await cache.requeue_expired_claims(now_ms=now, limit=100)
        late = await cache.requeue_expired_claims(now_ms=now + LEASE_MS + 1_000, limit=100)
        again = await claim(cache, now + LEASE_MS + 1_000, mine)
        logger.info("租约到期", requeued_early=early, requeued_late=late, reclaimed=len(again))

        # 3. 确认处理完成，这条申请永久移除，之后不会再被认领
        for apply_id in again:
            await cache.acknowledge_friend_expiration(apply_id)
        logger.info(
            "确认之后",
            requeued=await cache.requeue_expired_claims(now_ms=now + DAY_MS, limit=100),
        )

        # 4. 分布式锁测试：同一时间只有一个抢到；不是持有者不能释放锁
        got_a = await cache.acquire_scheduler_lock("scheduler-a", ttl_ms=5_000)
        got_b = await cache.acquire_scheduler_lock("scheduler-b", ttl_ms=5_000)
        wrong = await cache.release_scheduler_lock("scheduler-b")
        right = await cache.release_scheduler_lock("scheduler-a")
        logger.info(
            "调度锁",
            first=got_a,
            second=got_b,
            released_by_other=wrong,
            released_by_owner=right,
        )

        # 5. 收尾：把那条明天到期的也认领确认，方便脚本反复跑
        for apply_id in await claim(cache, now + DAY_MS + 1, mine):
            await cache.acknowledge_friend_expiration(apply_id)
    finally:
        await cache.aclose()  # 关闭Redis连接

if __name__ == "__main__":
    # 直接运行这个文件才执行
    configure_logging(json_output=False)  # 日志输出为可读文本，不是json
    asyncio.run(main())  # 启动异步入口，跑好友申请过期队列演示