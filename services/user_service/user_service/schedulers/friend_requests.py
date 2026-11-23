"""Lease-based friend-request expiration dispatcher backed by the outbox."""
# 基于租约机制的好友申请过期调度器，使用事务发件箱（outbox）保证事件可靠投递

import time  # 获取时间戳
from uuid import uuid4

from infinitechat.contracts.events import FriendRequestExpired, KafkaTopic
# 过期事件实体、Kafka主题枚举

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 异步数据库会话

from user_service.integrations.redis import UserRedis
# Redis封装，好友申请延迟队列、租约、分布式锁
from user_service.outbox import OutboxRepository
# 事务发件箱仓库，用来落库事件，再由outbox worker推Kafka

# 好友申请过期调度器
class FriendRequestExpirationScheduler:
    # 构造方法，注入依赖
    def __init__(
        self,
        *,  # 后面参数必须关键字传参
        session_factory: async_sessionmaker[AsyncSession],
        cache: UserRedis,
        batch_size: int = 500,  # 一轮最多处理多少条
        lease_ms: int = 30_000,  # 认领租约30秒
    ) -> None:
        self._sessions = session_factory
        self._cache = cache
        self._batch_size = batch_size
        self._lease_ms = lease_ms

    # 执行一轮调度，返回本轮处理数量
    """
        好友申请过期调度器（定时任务），分布式多实例环境下，自动扫描、处理超时没被同意 / 
        拒绝的好友申请，产出【好友申请过期事件】，通过发件箱模式投递到 Kafka。
    """
    async def run_once(self) -> int:
        owner = uuid4().hex  # 生成本轮持有者标识
        # 抢占分布式锁，抢不到直接退出，避免多实例同时跑
        if not await self._cache.acquire_scheduler_lock(owner, ttl_ms=5_000):
            return 0
        try:
            now_ms = time.time_ns() // 1_000_000  # 转为毫秒时间戳
            # 第一步：把之前认领后超时没处理的申请，放回待过期队列
            await self._cache.requeue_expired_claims(
                now_ms=now_ms,
                limit=self._batch_size,
            )
            # 第二步：认领一批到期的好友申请，移入inflight集合，带上租约
            claims = await self._cache.claim_due_friend_requests(
                now_ms=now_ms,
                limit=self._batch_size,
                lease_until_ms=now_ms + self._lease_ms,
            )
            processed = 0
            for apply_friend_id, expire_time in claims:
                # 构造过期事件
                event = FriendRequestExpired(
                    apply_friend_id=apply_friend_id,
                    expire_time=expire_time,
                )
                # 事件唯一ID，用来做幂等，重复执行不会重复入库
                event_id = f"friend-expired-{apply_friend_id}-{expire_time}"
                # 开启事务，往outbox写入事件，只不存在才新增
                async with self._sessions() as session, session.begin():
                    await OutboxRepository(session).add_if_absent(
                        event_id=event_id,
                        aggregate_type="friend_request",
                        aggregate_id=str(apply_friend_id),
                        event_type="FriendRequestExpired",
                        topic=KafkaTopic.FRIEND_REQUEST_EXPIRATION,
                        event_key=str(apply_friend_id),
                        payload=event.to_wire(),
                        headers={"event-id": event_id, "schema-version": "1"},
                    )
                # 写入outbox成功，确认这条申请处理完成，从inflight移除
                await self._cache.acknowledge_friend_expiration(apply_friend_id)
                processed += 1
            return processed
        finally:
            # 释放锁，只有抢到锁的owner才能释放
            await self._cache.release_scheduler_lock(owner)