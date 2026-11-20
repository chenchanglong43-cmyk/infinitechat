"""Typed Redis DB2 operations owned by UserService."""

# 导入异步redis客户端
from redis.asyncio import Redis

# access_token的redis key前缀
ACCESS_TOKEN_PREFIX = "access:token:"  # noqa: S105 - Redis key prefix, not a token
# refresh_token的redis key前缀
REFRESH_TOKEN_PREFIX = "refresh:token:"  # noqa: S105 - Redis key prefix, not a token
# 用户离线时间key前缀
OFFLINE_TIME_PREFIX = "user:offline:"
# 好友状态缓存key前缀
FRIEND_STATUS_PREFIX = "msg:validate:friend:status:"  # 常量 好友状态前缀

# Lua脚本：对比值相等才删除，保证原子性
_COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Lua脚本：对比值相等，删掉key并返回成功标记，原子执行
_COMPARE_GET_DELETE = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# 用户相关Redis操作封装类
class UserRedis:
    # 构造方法，把redis客户端存到实例里
    def __init__(self, client: Redis) -> None:
        self._client = client

    # 类方法，从redis连接url快速创建实例
    @classmethod
    def from_url(cls, url: str, *, database: int = 2) -> "UserRedis":
        # 连接redis，自动解码字符串，用redis协议2
        return cls(Redis.from_url(url, db=database, decode_responses=True, protocol=2))

    # 健康检查，ping一下redis看通不通
    async def ping(self) -> bool:
        return bool(await self._client.ping())

    # 保存验证码，nx=True 只有key不存在才写入，防止重复覆盖
    async def reserve_captcha(self, email: str, code: str, *, ttl_seconds: int = 300) -> bool:
        return bool(await self._client.set(email, code, ex=ttl_seconds, nx=True))

    # 消费验证码：值对上就删掉，用lua保证原子，防止并发问题
    async def consume_captcha(self, email: str, code: str) -> bool:
        return bool(await self._client.eval(_COMPARE_GET_DELETE, 1, email, code))

    # 释放验证码：值匹配才删除，比如手动作废验证码
    async def release_captcha(self, email: str, code: str) -> bool:
        return bool(await self._client.eval(_COMPARE_DELETE, 1, email, code))

    # 同时存access和refresh令牌，用管道打包，一次网络请求
    async def store_tokens(
        self,
        user_id: int,
        *,  # 星号后面的参数只能按名字传
        access_token: str,
        refresh_token: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> None:
        # 开启redis管道，transaction=True开启事务
        async with self._client.pipeline(transaction=True) as pipeline:
            # 写入access_token，带上过期时间
            pipeline.set(f"{ACCESS_TOKEN_PREFIX}{user_id}", access_token, ex=access_ttl_seconds)
            # 写入refresh_token，带上过期时间
            pipeline.set(f"{REFRESH_TOKEN_PREFIX}{user_id}", refresh_token, ex=refresh_ttl_seconds)
            # 批量执行所有命令
            await pipeline.execute()

    # 获取用户当前有效的access_token
    async def current_access_token(self, user_id: int) -> str | None:
        # 根据key去redis里拿值
        value = await self._client.get(f"{ACCESS_TOKEN_PREFIX}{user_id}")
        # 有值转字符串返回，没有就返回None
        return str(value) if value is not None else None

    # 获取用户当前有效的refresh_token
    async def current_refresh_token(self, user_id: int) -> str | None:
        value = await self._client.get(f"{REFRESH_TOKEN_PREFIX}{user_id}")
        return str(value) if value is not None else None

    # 吊销令牌，登录退出的时候调用，直接删掉两个token
    async def revoke_tokens(self, user_id: int) -> None:
        await self._client.delete(
            f"{ACCESS_TOKEN_PREFIX}{user_id}",
            f"{REFRESH_TOKEN_PREFIX}{user_id}",
        )

    # 获取并一次性删除离线时间，getdel是拿完就删
    async def take_offline_time(self, user_id: int) -> int | None:
        value = await self._client.getdel(f"{OFFLINE_TIME_PREFIX}{user_id}")
        # 拿到值转int，没有返回None
        return int(value) if value is not None else None

    # 读取缓存里的好友状态
    async def get_friend_status(self, user_id: int, friend_id: int) -> int | None:
        # 拼接key，去redis查
        value = await self._client.get(self.friend_status_key(user_id, friend_id))
        return int(value) if value is not None else None

    # 写入好友状态缓存，缓存5分钟，减少数据库查询
    async def cache_friend_status(self, user_id: int, friend_id: int, status: int) -> None:
        await self._client.set(
            self.friend_status_key(user_id, friend_id),
            str(status),
            ex=300,
        )

    # 删除好友状态缓存，双向都删，防止脏缓存
    async def evict_friend_status(self, user_id: int, friend_id: int) -> None:
        await self._client.delete(
            self.friend_status_key(user_id, friend_id),
            self.friend_status_key(friend_id, user_id),
        )

    # 静态方法，拼接好友状态的redis key
    @staticmethod
    def friend_status_key(user_id: int, friend_id: int) -> str:
        return f"{FRIEND_STATUS_PREFIX}{user_id}:{friend_id}"

    # 关闭redis客户端连接
    async def aclose(self) -> None:
        await self._client.aclose()