"""网关边缘鉴权演示.

只认 Redis 里的当前令牌: 被新登录顶掉的旧令牌直接拦下; access 不算数但 refresh 还是当前的,
告诉客户端去刷新; 拿 refresh 冒充 access, 篡改过的令牌, 统统按未登录处理.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 导入异步库，用来运行异步协程
import secrets  # 导入安全随机库，生成jti，防时序攻击
from datetime import timedelta  # 导入时间增量，用于设置JWT过期时长

import structlog  # 导入结构化日志库，打印带字段的日志

from infinitechat.foundation.settings import read_env  # 导入环境读取函数，读取.env和系统环境变量
from gateway.auth import GatewayAuthenticator, GatewayAuthError  # 导入网关认证器、认证自定义异常

from infinitechat.foundation.security import JwtCodec, TokenType  # 导入JWT编解码器、令牌类型枚举

from infinitechat.foundation.telemetry import configure_logging  # 导入日志初始化配置函数
from redis.asyncio import Redis  # 导入异步Redis客户端

from user_service.integrations.redis import UserRedis  # 导入用户业务Redis封装类

logger = structlog.get_logger(__name__)  # 获取日志实例

USER_ID = 9_000_007_001  # 定义本次测试使用的用户ID

# 按 user 服务签发令牌的写法签一个, 带随机 jti
def issue(codec: JwtCodec, token_type: TokenType, ttl_seconds: int) -> str:
    """按 user 服务签发令牌的写法签一个, 带随机 jti."""
    return codec.encode(  # 调用JWT编码方法生成令牌
        subject=str(USER_ID),  # sub载荷，存入用户ID字符串
        expires_in=timedelta(seconds=ttl_seconds),  # 设置令牌过期时间
        token_type=token_type,  # 设置令牌类型：ACCESS / REFRESH
        additional_claims={"jti": secrets.token_hex(16)},  # 附加jti随机标识，16字节十六进制
    )

# 模拟一次登录: 签一组令牌
async def login(codec: JwtCodec, store: UserRedis) -> tuple[str, str]:
    """模拟一次登录: 签一组令牌, 按 user 服务的写法存成当前令牌."""
    access = issue(codec, TokenType.ACCESS, 1800)  # 生成access令牌，有效期30分钟
    refresh = issue(codec, TokenType.REFRESH, 604_800)  # 生成refresh令牌，有效期7天
    await store.store_tokens(  # 异步写入Redis，保存当前有效令牌
        USER_ID,  # 测试用户ID
        access_token=access,  # 传入access令牌
        refresh_token=refresh,  # 传入refresh令牌
        access_ttl_seconds=1800,  # access在Redis的过期时间
        refresh_ttl_seconds=604_800,  # refresh在Redis的过期时间
    )
    return access, refresh  # 返回生成好的access、refresh令牌

# 拿一组请求头去认证, 打出放行还是拦下
async def check(
    authenticator: GatewayAuthenticator,
    case: str,
    *,  # 标记后面参数必须使用关键字传参
    access: str,
    refresh: str | None = None,
) -> None:
    """拿一组请求头去认证, 打出放行还是拦下."""
    try:  # 捕获认证过程抛出的异常
        user_id = await authenticator.authenticate(access_token=access, refresh_token=refresh)  # 调用网关认证逻辑
        logger.info("放行", case=case, user_matches=user_id == USER_ID)  # 认证通过，打印放行日志
    except GatewayAuthError as error:  # 捕获网关认证异常
        logger.warning("拦下", case=case, error_code=int(error.code), message=error.message)  # 认证失败，打印拦截告警日志

# 把签名正中间的一个字符换掉
def tamper(token: str) -> str:
    """把签名正中间的一个字符换掉，模拟篡改令牌。"""
    head, payload, signature = token.split(".")  # JWT按.分割为头部、载荷、签名三段
    middle = len(signature) // 2  # 算出签名字符串中间位置下标
    swapped = "A" if signature[middle] != "A" else "B"  # 替换中间字符，制造无效签名
    return ".".join([head, payload, signature[:middle] + swapped + signature[middle + 1 :]])  # 拼接成篡改后的JWT

# 同一个用户先后在两台设备上登录
async def main() -> None:
    """同一个用户先后在两台设备上登录, 拿各种令牌组合去敲网关."""
    redis = Redis.from_url(read_env("REDIS_URL"), db=2, decode_responses=True, protocol=2)  # 连接Redis，读取环境里的连接地址
    codec = JwtCodec(read_env("JWT_SECRET"))  # 初始化JWT编解码器，加载密钥
    authenticator = GatewayAuthenticator(codec, redis)  # 实例化网关认证器
    store = UserRedis(redis)  # 实例化用户Redis存储封装
    try:  # 主逻辑块，最后要关闭连接
        old_access, _ = await login(codec, store)  # 第一次登录，拿到旧access令牌
        await check(authenticator, "当前令牌", access=old_access)  # 测试：当前有效令牌
        # 另一台设备登录，Redis里的当前令牌被覆盖
        new_access, new_refresh = await login(codec, store)  # 第二次登录，生成新令牌，覆盖Redis记录
        await check(authenticator, "被顶掉的旧令牌", access=old_access)  # 测试：已被顶下线的旧access
        await check(
            authenticator,
            "旧 access 加当前 refresh",
            access=old_access,
            refresh=new_refresh,
        )  # 测试：access失效、refresh有效的组合
        await check(authenticator, "refresh 冒充 access", access=new_refresh)  # 测试：拿refresh当access传
        await check(authenticator, "篡改过的令牌", access=tamper(new_access))  # 测试：签名被篡改的令牌
    finally:  # 无论正常还是异常，都执行关闭连接
        await redis.aclose()  # 关闭Redis异步连接

if __name__ == "__main__":  # 判断是否直接运行本文件
    configure_logging(json_output=False)  # 初始化日志，关闭JSON格式输出
    asyncio.run(main())  # 启动异步事件循环，执行main协程