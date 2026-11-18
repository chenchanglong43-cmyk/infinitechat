"""Typed Redis DB2 operations owned by UserService."""

from redis.asyncio import Redis

ACCESS_TOKEN_PREFIX = "access:token:"  # noqa: S105 - Redis key prefix, not a token
REFRESH_TOKEN_PREFIX = "refresh:token:"  # noqa: S105 - Redis key prefix, not a token
OFFLINE_TIME_PREFIX = "user:offline:"

# 比较删除: 下面这段 Lua 脚本在 Redis 里一次性原子执行
_COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# 比较获取删除: 下面这段 Lua 脚本在 Redis 里一次性原子执行
_COMPARE_GET_DELETE = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# 用户模块Redis操作封装类
class UserRedis:
    # 构造函数：接收redis客户端实例，存到内部变量
    def __init__(self, client: Redis) -> None:
        self._client = client

    # 类方法：通过redis连接url快速创建实例，默认使用db2
    @classmethod
    def from_url(cls, url: str, *, database: int = 2) -> "UserRedis":
        # decode_responses=True 自动把返回值转成字符串，不用手动encode/decode
        return cls(Redis.from_url(url, db=database, decode_responses=True, protocol=2))

    # 健康检查：ping redis，判断连通性
    async def ping(self) -> bool:
        return bool(await self._client.ping())

    # 预留验证码：nx=True 只有key不存在才写入，防止重复发验证码；ex设置过期时间
    async def reserve_captcha(self, email: str, code: str, *, ttl_seconds: int = 300) -> bool:
        return bool(await self._client.set(email, code, ex=ttl_seconds, nx=True))

    # 消费验证码：用Lua脚本原子对比+删除，验证码匹配才删掉，防止并发问题
    async def consume_captcha(self, email: str, code: str) -> bool:
        return bool(await self._client.eval(_COMPARE_GET_DELETE, 1, email, code))

    # 释放验证码：对比值一致才删除，比如验证码没用到、手动作废
    async def release_captcha(self, email: str, code: str) -> bool:
        return bool(await self._client.eval(_COMPARE_DELETE, 1, email, code))

    # 保存access_token和refresh_token，用pipeline批量提交，减少网络往返
    async def store_tokens(
        self,
        user_id: int,
        *,  # 后面参数强制关键字传参，避免传参顺序搞错
        access_token: str,
        refresh_token: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> None:
        # 开启redis管道，transaction=True 开启事务
        async with self._client.pipeline(transaction=True) as pipeline:
            pipeline.set(f"{ACCESS_TOKEN_PREFIX}{user_id}", access_token, ex=access_ttl_seconds)
            pipeline.set(f"{REFRESH_TOKEN_PREFIX}{user_id}", refresh_token, ex=refresh_ttl_seconds)
            await pipeline.execute()

    # 获取用户当前有效的access_token
    async def current_access_token(self, user_id: int) -> str | None:
        value = await self._client.get(f"{ACCESS_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 获取用户当前有效的refresh_token
    async def current_refresh_token(self, user_id: int) -> str | None:
        value = await self._client.get(f"{REFRESH_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 吊销令牌：登录/改密/退出时，直接删掉用户的两个token，强制下线
    async def revoke_tokens(self, user_id: int) -> None:
        await self._client.delete(
            f"{ACCESS_TOKEN_PREFIX}{user_id}",
            f"{REFRESH_TOKEN_PREFIX}{user_id}",
        )

    # 获取并一次性删除下线时间记录（getdel 读了就删，原子操作）
    async def take_offline_time(self, user_id: int) -> int | None:
        value = await self._client.getdel(f"{OFFLINE_TIME_PREFIX}{user_id}")
        return int(value) if value is not None else None

    # 关闭redis异步连接
    async def aclose(self) -> None:
        await self._client.aclose()