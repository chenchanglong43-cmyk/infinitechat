"""WebSocket 握手鉴权演示.

按接口约定: 握手时在 Authorization 头里带 access 令牌, 签名对了还要是 Redis 里的当前令牌;
被新登录顶掉的旧令牌、refresh 令牌、没带令牌都握不了手.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

# 异步协程库
import asyncio
# 生成安全随机串
import secrets
# 时间间隔，用来设置jwt过期时间
from datetime import timedelta

# 结构化日志组件
import structlog

# 读取环境变量，优先读.env
from infinitechat.foundation.settings import read_env
# jwt编解码、令牌类型枚举
from infinitechat.foundation.security import JwtCodec, TokenType
# 链路/日志初始化
from infinitechat.foundation.telemetry import configure_logging
# 我们写好的ws鉴权类
from realtime_service.auth import RealtimeAuthenticator
# 异步redis客户端
from redis.asyncio import Redis
# 用户服务封装的redis操作类
from user_service.integrations.redis import UserRedis

# 获取日志实例
logger = structlog.get_logger(__name__)

# 固定测试用户id
USER_ID = 9_000_009_001


# 模仿user服务签发令牌的逻辑，带上jti唯一标识
def issue(codec: JwtCodec, token_type: TokenType, ttl_seconds: int) -> str:
    """按 user 服务签发令牌的写法签一个, 带随机 jti."""
    return codec.encode(
        subject=str(USER_ID),
        expires_in=timedelta(seconds=ttl_seconds),
        token_type=token_type,
        additional_claims={"jti": secrets.token_hex(16)},
    )


# 模拟登录：生成access、refresh，并存到redis里覆盖当前有效令牌
async def login(codec: JwtCodec, store: UserRedis) -> tuple[str, str]:
    """模拟一次登录: 签一组令牌, 按 user 服务的写法存成当前令牌."""
    access = issue(codec, TokenType.ACCESS, 1800)
    refresh = issue(codec, TokenType.REFRESH, 604_800)
    await store.store_tokens(
        USER_ID,
        access_token=access,
        refresh_token=refresh,
        access_ttl_seconds=1800,
        refresh_ttl_seconds=604_800,
    )
    return access, refresh


# 主测试流程：多次登录，测试各种令牌场景
async def main() -> None:
    """登录两次, 拿新旧令牌分别去握手."""
    # 连接redis，用2号库，自动解码字符串
    redis = Redis.from_url(read_env("REDIS_URL"), db=2, decode_responses=True, protocol=2)
    # 初始化jwt编解码器
    codec = JwtCodec(read_env("JWT_SECRET"))
    # 实例化ws鉴权器，注入依赖
    auth = RealtimeAuthenticator(codec, redis)
    try:
        # 第一次登录，拿到第一版access令牌
        old_access, _old_refresh = await login(codec, UserRedis(redis))
        # 场景1：当前有效令牌，鉴权应该通过
        logger.info("当前令牌", user_ok=await auth.authenticate(old_access) == USER_ID)
        # 第二次登录，redis会覆盖access，旧令牌被顶下线
        access, refresh = await login(codec, UserRedis(redis))
        # 场景2：已经被顶掉的旧access，签名没问题，但redis里不匹配，鉴权失败
        logger.warning("被顶掉的旧令牌", result=await auth.authenticate(old_access))
        # 场景3：新登录的access，正常放行
        logger.info("新令牌", user_ok=await auth.authenticate(access) == USER_ID)
        # 场景4：refresh令牌，不允许用来连ws
        logger.warning("refresh 令牌", result=await auth.authenticate(refresh))
        # 场景5：请求头不带令牌，直接拒绝
        logger.warning("没带令牌", result=await auth.authenticate(None))
    finally:
        # 不管测试成功还是报错，最后都关闭redis连接，防止泄漏
        await redis.aclose()


# 直接运行这个脚本才会执行测试逻辑，导入成模块时不跑
if __name__ == "__main__":
    configure_logging(json_output=False)  # 日志不输出json，方便控制台看
    asyncio.run(main())  # 启动异步事件循环，跑测试逻辑