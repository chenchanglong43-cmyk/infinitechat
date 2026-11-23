"""Typed Redis DB2 operations owned by UserService."""
# 用户服务专用的Redis DB2封装，所有Redis操作统一收口在这里

from collections.abc import Sequence

from redis.asyncio import Redis

ACCESS_TOKEN_PREFIX = "access:token:"  # noqa: S105 - Redis key前缀，不是token本身
REFRESH_TOKEN_PREFIX = "refresh:token:"  # noqa: S105 - Redis key前缀，不是token本身
OFFLINE_TIME_PREFIX = "user:offline:"  # 用户下线时间key前缀
FRIEND_STATUS_PREFIX = "msg:validate:friend:status:"  # 好友校验状态缓存key前缀
FRIEND_REQUEST_DUE_ZSET = "friend-request-expire-zset"  # 待过期处理的好友申请有序集合
FRIEND_REQUEST_INFLIGHT_ZSET = "friend-request-expire-inflight-zset"  # 已经认领、正在处理的好友申请有序集合
FRIEND_REQUEST_SCHEDULER_LOCK = "lock:user:friend-request-expiration"  # 好友申请过期调度器分布式锁

# 比较删除Lua脚本：key的值等于传入值才删，原子操作，防止误删
_COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# 比较获取删除Lua脚本：值匹配就删掉，用来消费验证码
_COMPARE_GET_DELETE = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# 认领到期Lua脚本：从到期zset捞一批，移到处理中zset，加租约，防止多实例抢同一条
_CLAIM_DUE = """
local items = redis.call(
  'ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'WITHSCORES', 'LIMIT', 0, ARGV[2]
)
local claimed = {}
for index = 1, #items, 2 do
  local item = items[index]
  local original_score = items[index + 1]
  if redis.call('ZREM', KEYS[1], item) == 1 then
    redis.call('ZADD', KEYS[2], ARGV[3], item)
    table.insert(claimed, item)
    table.insert(claimed, original_score)
  end
end
return claimed
"""

# 重新入队过期Lua脚本：处理中超时的申请，放回待过期队列，重新走流程
_REQUEUE_EXPIRED = """
local items = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
local moved = 0
for _, item in ipairs(items) do
  if redis.call('ZREM', KEYS[1], item) == 1 then
    redis.call('ZADD', KEYS[2], ARGV[1], item)
    moved = moved + 1
  end
end
return moved
"""

# 用户Redis操作封装类
class UserRedis:
    # 构造方法，注入redis客户端
    def __init__(self, client: Redis) -> None:
        self._client = client

    # 类方法，从redis连接url快速创建实例
    @classmethod
    def from_url(cls, url: str, *, database: int = 2) -> "UserRedis":
        return cls(Redis.from_url(url, db=database, decode_responses=True, protocol=2))

    # redis连通性健康检查
    async def ping(self) -> bool:
        return bool(await self._client.ping())

    # 预留验证码，nx=True保证一个邮箱只能存一个，防止重复覆盖
    async def reserve_captcha(self, email: str, code: str, *, ttl_seconds: int = 300) -> bool:
        return bool(await self._client.set(email, code, ex=ttl_seconds, nx=True))

    # 消费验证码，值对上才删除，原子Lua脚本
    async def consume_captcha(self, email: str, code: str) -> bool:
        return bool(await self._client.eval(_COMPARE_GET_DELETE, 1, email, code))

    # 释放验证码，匹配才删，用于业务回滚
    async def release_captcha(self, email: str, code: str) -> bool:
        return bool(await self._client.eval(_COMPARE_DELETE, 1, email, code))

    # 批量存入access和refresh令牌，用pipeline一次性提交
    async def store_tokens(
        self,
        user_id: int,
        *,
        access_token: str,
        refresh_token: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> None:
        async with self._client.pipeline(transaction=True) as pipeline:
            pipeline.set(f"{ACCESS_TOKEN_PREFIX}{user_id}", access_token, ex=access_ttl_seconds)
            pipeline.set(f"{REFRESH_TOKEN_PREFIX}{user_id}", refresh_token, ex=refresh_ttl_seconds)
            await pipeline.execute()

    # 查询用户当前有效的access令牌
    async def current_access_token(self, user_id: int) -> str | None:
        value = await self._client.get(f"{ACCESS_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 查询用户当前有效的refresh令牌
    async def current_refresh_token(self, user_id: int) -> str | None:
        value = await self._client.get(f"{REFRESH_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 吊销用户全部令牌，登出场景用
    async def revoke_tokens(self, user_id: int) -> None:
        await self._client.delete(
            f"{ACCESS_TOKEN_PREFIX}{user_id}",
            f"{REFRESH_TOKEN_PREFIX}{user_id}",
        )

    # 取出并删除下线时间，getdel是原子的，取完就清
    async def take_offline_time(self, user_id: int) -> int | None:
        value = await self._client.getdel(f"{OFFLINE_TIME_PREFIX}{user_id}")
        return int(value) if value is not None else None

    # 读取缓存的好友校验状态
    async def get_friend_status(self, user_id: int, friend_id: int) -> int | None:
        value = await self._client.get(self.friend_status_key(user_id, friend_id))
        return int(value) if value is not None else None

    # 缓存好友校验状态，有效期5分钟
    async def cache_friend_status(self, user_id: int, friend_id: int, status: int) -> None:
        await self._client.set(
            self.friend_status_key(user_id, friend_id),
            str(status),
            ex=300,
        )

    # 清除双向好友缓存，A-B和B-A都删掉
    async def evict_friend_status(self, user_id: int, friend_id: int) -> None:
        await self._client.delete(
            self.friend_status_key(user_id, friend_id),
            self.friend_status_key(friend_id, user_id),
        )

    # 静态方法，拼接好友状态的redis key
    @staticmethod
    def friend_status_key(user_id: int, friend_id: int) -> str:
        return f"{FRIEND_STATUS_PREFIX}{user_id}:{friend_id}"

    # 把好友申请加入过期等待zset，分数是过期时间戳(毫秒)
    async def enqueue_friend_expiration(self, apply_friend_id: int, expire_time_ms: int) -> None:
        await self._client.zadd(FRIEND_REQUEST_DUE_ZSET, {str(apply_friend_id): expire_time_ms})

    # 把处理中超时的申请，放回待过期队列
    async def requeue_expired_claims(self, *, now_ms: int, limit: int) -> int:
        return int(
            await self._client.eval(
                _REQUEUE_EXPIRED,
                2,
                FRIEND_REQUEST_INFLIGHT_ZSET,
                FRIEND_REQUEST_DUE_ZSET,
                now_ms,
                limit,
            )
        )

    # 认领一批到期好友申请，移到inflight集合，带上租约时间
    async def claim_due_friend_requests(
        self,
        *,
        now_ms: int,
        limit: int,
        lease_until_ms: int,
    ) -> tuple[tuple[int, int], ...]:
        values: Sequence[str] = await self._client.eval(
            _CLAIM_DUE,
            2,
            FRIEND_REQUEST_DUE_ZSET,
            FRIEND_REQUEST_INFLIGHT_ZSET,
            now_ms,
            limit,
            lease_until_ms,
        )
        return tuple(
            (int(values[index]), int(float(values[index + 1])))
            for index in range(0, len(values), 2)
        )

    # 确认好友申请过期处理完成，从inflight集合删掉
    async def acknowledge_friend_expiration(self, apply_friend_id: int) -> None:
        await self._client.zrem(FRIEND_REQUEST_INFLIGHT_ZSET, str(apply_friend_id))

    # 抢占调度器分布式锁，nx保证只有一个抢到，px自动过期
    async def acquire_scheduler_lock(self, owner: str, *, ttl_ms: int) -> bool:
        return bool(
            await self._client.set(
                FRIEND_REQUEST_SCHEDULER_LOCK,
                owner,
                nx=True,
                px=ttl_ms,
            )
        )

    # 释放调度锁，只有持有者才能删，防止误释放别人的锁
    async def release_scheduler_lock(self, owner: str) -> bool:
        return bool(
            await self._client.eval(_COMPARE_DELETE, 1, FRIEND_REQUEST_SCHEDULER_LOCK, owner)
        )

    # 关闭redis连接
    async def aclose(self) -> None:
        await self._client.aclose()