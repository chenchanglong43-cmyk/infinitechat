"""Typed Redis DB2 operations owned by UserService."""
# UserService 自己管理的 Redis 操作封装（默认用 2 号库）
# 把散落在各处的 Redis 命令收拢到这里：验证码、登录令牌、好友状态缓存、好友申请延迟队列、调度锁

from collections.abc import Sequence  # 序列类型：Lua 返回的列表用它标注

from infinitechat.foundation.ids import LeaseStore  # 租约存储协议：雪花 worker_id 占位用
from redis.asyncio import Redis  # 异步 Redis 客户端

ACCESS_TOKEN_PREFIX = "access:token:"  # noqa: S105 - Redis key prefix, not a token
# 访问令牌的 key 前缀：access:token:{user_id}
REFRESH_TOKEN_PREFIX = "refresh:token:"  # noqa: S105 - Redis key prefix, not a token
# 刷新令牌的 key 前缀：refresh:token:{user_id}
OFFLINE_TIME_PREFIX = "user:offline:"
# 离线时间 key 前缀：user:offline:{user_id}，记录用户最后一次下线时间
FRIEND_STATUS_PREFIX = "msg:validate:friend:status:"  # 常量 好友状态前缀
# 好友状态缓存 key 前缀：msg:validate:friend:status:{user_id}:{friend_id}
FRIEND_REQUEST_DUE_ZSET = "friend-request-expire-zset"
# 好友申请“到期待办”有序集合：member=申请ID，score=到期时间
FRIEND_REQUEST_INFLIGHT_ZSET = "friend-request-expire-inflight-zset"
# 好友申请“处理中”有序集合：member=申请ID，score=认领租约到期时间
FRIEND_REQUEST_SCHEDULER_LOCK = "lock:user:friend-request-expiration"
# 调度器分布式锁的 key：同一时刻只允许一个实例跑过期扫描

# 比较删除: 下面这段 Lua 脚本在 Redis 里一次性原子执行
# 用途：只有 key 当前的值等于我预期的值，才删除；防止误删别人的锁/验证码
_COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# 比较获取删除: 下面这段 Lua 脚本在 Redis 里一次性原子执行
# 用途：验证码“一次性消费”——值匹配就删掉并返回 1，防止同一个验证码用两次
_COMPARE_GET_DELETE = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# 认领到期: 下面这段 Lua 脚本在 Redis 里一次性原子执行
# 用途：从“待办”集合取一批到期的申请，原子搬到“处理中”集合，并给它们盖上租约时间
_CLAIM_DUE = """
local items = redis.call(

  'ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'WITHSCORES', 'LIMIT', 0, ARGV[2]
)  -- )
-- 上面：从待办集合里取 score <= 当前时间 的成员，带分数，最多取 limit 个
local claimed = {}
for index = 1, #items, 2 do
  local item = items[index]
  local original_score = items[index + 1]
  -- 从待办里删掉；删成功（返回 1）说明这条被我抢到了
  if redis.call('ZREM', KEYS[1], item) == 1 then
    -- 搬到处理中集合，score 改成“租约到期时间”
    redis.call('ZADD', KEYS[2], ARGV[3], item)
    table.insert(claimed, item)  -- table.insert(claimed, it
    table.insert(claimed, original_score)  -- table.insert(claimed, or
  end
end
-- 返回 [申请ID, 原到期时间, 申请ID, 原到期时间, ...] 的扁平列表
return claimed
"""

# 重新入队已过期: 下面这段 Lua 脚本在 Redis 里一次性原子执行
# 用途：把“处理中”里租约已经超时的申请搬回“待办”，让别的实例可以重新认领
_REQUEUE_EXPIRED = """
-- 局部变量 items: 按分数取一批, 第 1 个键, 第 1 个参数...
local items = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
-- 上面：从处理中集合里取 score（租约到期时间）<= 当前时间 的成员
local moved = 0
for _, item in ipairs(items) do
  -- 从处理中删掉；删成功说明这条确实还在处理中
  if redis.call('ZREM', KEYS[1], item) == 1 then
    -- 放回待办，score 重置为当前时间，下一轮就能被重新认领
    redis.call('ZADD', KEYS[2], ARGV[1], item)
    moved = moved + 1  -- moved = moved + 1
  end
end
-- 返回搬回去的条数
return moved
"""

# 用户Redis封装
class UserRedis:
    # 初始化
    def __init__(self, client: Redis) -> None:  # 构造方法: 创建对象时把依赖存起来
        self._client = client  # 存下底层 Redis 客户端，后面所有命令都靠它

    # 租约存储
    @property  # 只读属性: 像字段一样访问
    # 租约存储
    def lease_store(self) -> LeaseStore:
        # 直接拿 Redis 客户端当租约存储用（它本身就有 set / eval）
        return self._client  # type: ignore[return-value]

    # 从URL创建
    @classmethod  # 类方法: 第一个参数是类本身
    # 从URL创建
    def from_url(cls, url: str, *, database: int = 2) -> "UserRedis":
        # decode_responses=True：读出来直接是 str，不用自己 decode
        # protocol=2：用 RESP2 协议，兼容性更好
        return cls(Redis.from_url(url, db=database, decode_responses=True, protocol=2))

    # 健康检查
    async def ping(self) -> bool:
        # 能给 Redis 发 ping 并收到响应，就说明连接正常
        return bool(await self._client.ping())

    # 预留验证码
    async def reserve_captcha(self, email: str, code: str, *, ttl_seconds: int = 300) -> bool:
        # SET email code NX EX 300：邮箱还没有验证码时才写入
        # 写成功说明“占位”成功，可以发邮件；写失败说明刚发过，别重复发
        return bool(await self._client.set(email, code, ex=ttl_seconds, nx=True))

    # 消费验证码
    async def consume_captcha(self, email: str, code: str) -> bool:
        # Lua 原子执行：值对得上就删掉并返回 1；对不上返回 0
        # 保证同一个验证码只能用一次，并发下也不会被用两次
        return bool(await self._client.eval(_COMPARE_GET_DELETE, 1, email, code))

    # 释放验证码
    async def release_captcha(self, email: str, code: str) -> bool:
        # 发邮件失败时调用：只有 Redis 里还是这个验证码，才删掉占位
        # 防止把别人刚写进去的新验证码误删
        return bool(await self._client.eval(_COMPARE_DELETE, 1, email, code))

    # 存储令牌
    async def store_tokens(
        self,
        user_id: int,
        *,  # 星号后面的参数只能按名字传
        access_token: str,
        refresh_token: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> None:
        # 开 Redis 批量管道 pipeline, 用完自动收尾
        # transaction=True：两条命令一起提交，避免只写了一条
        async with self._client.pipeline(transaction=True) as pipeline:
            # 写访问令牌，按配置的短有效期过期
            pipeline.set(f"{ACCESS_TOKEN_PREFIX}{user_id}", access_token, ex=access_ttl_seconds)
            # 写刷新令牌，按配置的长有效期过期
            pipeline.set(f"{REFRESH_TOKEN_PREFIX}{user_id}", refresh_token, ex=refresh_ttl_seconds)
            # 真正执行管道里的两条命令
            await pipeline.execute()

    # 当前访问令牌
    async def current_access_token(self, user_id: int) -> str | None:
        # 读 Redis 里当前有效的访问令牌；没有就返回 None
        value = await self._client.get(f"{ACCESS_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 当前刷新令牌
    async def current_refresh_token(self, user_id: int) -> str | None:
        # 读 Redis 里当前有效的刷新令牌；没有就返回 None
        value = await self._client.get(f"{REFRESH_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 吊销令牌
    async def revoke_tokens(self, user_id: int) -> None:
        # 一次删掉访问令牌和刷新令牌
        # 删完以后，旧 JWT 即使没过期，和 Redis 比对也会失败，相当于强制下线
        await self._client.delete(
            f"{ACCESS_TOKEN_PREFIX}{user_id}",
            f"{REFRESH_TOKEN_PREFIX}{user_id}",
        )

    # 取下线时间
    async def take_offline_time(self, user_id: int) -> int | None:
        # GETDEL：取出离线时间的同时把 key 删掉，保证只被消费一次
        value = await self._client.getdel(f"{OFFLINE_TIME_PREFIX}{user_id}")
        return int(value) if value is not None else None

    # 获取好友状态
    async def get_friend_status(self, user_id: int, friend_id: int) -> int | None:
        # 读缓存里的好友状态；没缓存返回 None
        value = await self._client.get(self.friend_status_key(user_id, friend_id))
        return int(value) if value is not None else None

    # 缓存好友状态
    async def cache_friend_status(self, user_id: int, friend_id: int, status: int) -> None:
        # 写好友状态缓存，固定 5 分钟过期
        # 拉黑、删除好友时会主动清掉，避免长时间用旧状态
        await self._client.set(
            self.friend_status_key(user_id, friend_id),
            str(status),
            ex=300,
        )

    # 清除好友状态
    async def evict_friend_status(self, user_id: int, friend_id: int) -> None:
        # 双向都删：A→B 和 B→A 的缓存一起清
        # 因为拉黑是单向的，两个方向的状态可能不一样
        await self._client.delete(
            self.friend_status_key(user_id, friend_id),
            self.friend_status_key(friend_id, user_id),
        )

    # 好友状态键
    @staticmethod  # 静态方法: 不需要 self
    # 好友状态键
    def friend_status_key(user_id: int, friend_id: int) -> str:
        # 拼 key：前缀 + 用户ID + 好友ID
        return f"{FRIEND_STATUS_PREFIX}{user_id}:{friend_id}"

    # 入队申请过期
    async def enqueue_friend_expiration(self, apply_friend_id: int, expire_time_ms: int) -> None:
        # ZADD 待办集合：申请ID 当成员，到期时间当分数
        # 分数到了，调度器就能把它捞出来
        await self._client.zadd(FRIEND_REQUEST_DUE_ZSET, {str(apply_friend_id): expire_time_ms})

    # 回队过期认领
    async def requeue_expired_claims(self, *, now_ms: int, limit: int) -> int:
        # 调 Lua：把处理中里租约过期的搬回待办，返回搬了多少条
        # KEYS[1]=处理中集合，KEYS[2]=待办集合
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

    # 认领到期申请
    async def claim_due_friend_requests(
        self,
        *,  # 星号后面的参数只能按名字传
        now_ms: int,
        limit: int,
        lease_until_ms: int,
    ) -> tuple[tuple[int, int], ...]:
        # 调 Lua：从待办取最多 limit 条到期的，搬到处理中，并盖上租约时间
        values: Sequence[str] = await self._client.eval(  # 值(字符串列表): 等执行 Lua 脚本
            _CLAIM_DUE,
            2,
            FRIEND_REQUEST_DUE_ZSET,
            FRIEND_REQUEST_INFLIGHT_ZSET,
            now_ms,
            limit,
            lease_until_ms,
        )
        # Lua 返回的是扁平列表 [id, score, id, score...]，这里两两配对转成整数
        return tuple(
            (int(values[index]), int(float(values[index + 1])))  # (转成整数, 转成整数)
            for index in range(0, len(values), 2)
        )

    # 确认申请过期
    async def acknowledge_friend_expiration(self, apply_friend_id: int) -> None:
        # 处理完成，把申请从“处理中”集合删掉，表示不用再回收了
        await self._client.zrem(FRIEND_REQUEST_INFLIGHT_ZSET, str(apply_friend_id))

    # 获取调度锁
    async def acquire_scheduler_lock(self, owner: str, *, ttl_ms: int) -> bool:
        # SET NX PX：只有锁不存在时才能抢到；px 是毫秒级过期时间
        # 抢到返回 True，抢不到返回 False
        return bool(
            await self._client.set(
                FRIEND_REQUEST_SCHEDULER_LOCK,
                owner,
                nx=True,
                px=ttl_ms,
            )
        )

    # 释放调度锁
    async def release_scheduler_lock(self, owner: str) -> bool:
        # Lua 比对：锁里的 owner 还是我，才允许删；防止误删别人的锁
        return bool(
            await self._client.eval(_COMPARE_DELETE, 1, FRIEND_REQUEST_SCHEDULER_LOCK, owner)
        )

    # 关闭连接
    async def aclose(self) -> None:
        # 服务关闭时断开 Redis 连接，释放连接池
        await self._client.aclose()
