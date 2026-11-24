"""Gateway token validation against JWT and Redis current-session truth."""
# 网关JWT令牌校验，结合Redis校验当前会话有效性

import secrets  # 安全比较随机数，防止时序攻击

from infinitechat.contracts import ErrorCode, error_message  # 导入错误码、错误信息工具

from infinitechat.foundation.security import JwtCodec, TokenType  # 导入JWT编解码器、令牌类型枚举
from redis.asyncio import Redis  # 导入异步Redis客户端

ACCESS_TOKEN_PREFIX = "access:token:"  # noqa: S105 - Redis里access token的key前缀
REFRESH_TOKEN_PREFIX = "refresh:token:"  # noqa: S105 - Redis里refresh token的key前缀

# 网关认证错误
# 网关认证自定义异常
class GatewayAuthError(RuntimeError):
    # 初始化
    def __init__(self, code: ErrorCode, message: str | None = None) -> None:
        super().__init__(message or error_message(code))
        self.code = code
        self.message = message or error_message(code)

# 网关认证器
class GatewayAuthenticator:
    # 初始化
    def __init__(self, codec: JwtCodec, redis: Redis) -> None:  # 注入JWT编解码器、Redis实例
        self._codec = codec
        self._redis = redis

    # 认证令牌
    async def authenticate(
        self,
        *,  # 后续参数必须关键字传参
        access_token: str | None,
        refresh_token: str | None,
    ) -> int:
        try:
            # 先校验access令牌
            user_id = await self._validate_current(
                access_token,
                expected_type=TokenType.ACCESS,
                redis_prefix=ACCESS_TOKEN_PREFIX,
            )
            if user_id is not None:
                return user_id
            # access无效，再校验refresh令牌
            refresh_user_id = await self._validate_current(
                refresh_token,
                expected_type=TokenType.REFRESH,
                redis_prefix=REFRESH_TOKEN_PREFIX,
            )
            if refresh_user_id is not None:
                raise GatewayAuthError(ErrorCode.TOKEN_EXPIRED)
            raise GatewayAuthError(ErrorCode.NOT_LOGIN_ERROR)
        except GatewayAuthError:
            raise  # 直接抛出已定义的认证异常
        except Exception as error:
            # 其他未知异常包装为系统错误，带上原始异常链
            raise GatewayAuthError(
                ErrorCode.SYSTEM_ERROR,
                "登录状态确认失败",
            ) from error

    # 校验当前令牌
    async def _validate_current(
        self,
        token: str | None,
        *,  # 后续参数必须关键字传参
        expected_type: TokenType,
        redis_prefix: str,
    ) -> int | None:
        if not token:
            return None
        try:
            # 解码JWT
            claims = self._codec.decode(token)
            # 令牌类型不匹配直接返回空
            if claims.token_type not in {None, expected_type}:
                return None
            user_id = int(claims.sub)
        except ValueError:
            return None
        # 从Redis取出该用户当前有效的令牌
        current = await self._redis.get(f"{redis_prefix}{user_id}")
        current_token = str(current) if current is not None else ""
        # 安全对比令牌，防止时序攻击；一致返回用户ID，否则返回None
        return user_id if secrets.compare_digest(token, current_token) else None