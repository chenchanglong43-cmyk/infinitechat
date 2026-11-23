"""Idempotent friend-request Kafka handlers."""
# 好友申请Kafka事件处理器，支持幂等，处理新建、过期事件

from infinitechat.contracts.enums import FriendApplicationStatus
# 好友申请状态枚举

from infinitechat.contracts.events import FriendRequestCreated, FriendRequestExpired
# Kafka事件契约：申请创建、申请过期

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# 异步数据库会话

from user_service.integrations.redis import UserRedis
# Redis封装，用来维护过期延迟队列

from user_service.storage.repositories import FriendApplicationRepository
# 好友申请仓库，封装数据库状态变更逻辑

# 好友申请处理器
class FriendRequestHandlers:
    # 构造方法，注入会话工厂和Redis缓存
    def __init__(
        self,
        *,  # 后面参数必须关键字传参
        session_factory: async_sessionmaker[AsyncSession],
        cache: UserRedis,
    ) -> None:
        self._sessions = session_factory
        self._cache = cache

    # 收到【好友申请创建事件】，把申请ID入队Redis过期zset
    async def enqueue(self, event: FriendRequestCreated) -> None:
        await self._cache.enqueue_friend_expiration(
            event.apply_friend_id,
            event.expire_time,
        )

    # 收到【好友申请过期事件】，更新数据库状态为已过期
    async def expire(self, event: FriendRequestExpired) -> bool:
        # 开启数据库会话+事务，异常自动回滚
        async with self._sessions() as session, session.begin():
            # 状态流转：只有未读/已读才允许变成过期，其他状态跳过，保证幂等
            return await FriendApplicationRepository(session).transition(
                event.apply_friend_id,
                from_statuses=(
                    FriendApplicationStatus.UNREAD,
                    FriendApplicationStatus.READ,
                ),
                to_status=FriendApplicationStatus.EXPIRED,
            )