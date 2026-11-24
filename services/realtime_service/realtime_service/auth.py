"""WebSocket handshake authentication."""

# 安全对比随机工具，防止时序攻击
import secrets

# JWT编解码工具、令牌类型枚举
from infinitechat.foundation.security import JwtCodec, TokenType
# 异步redis客户端
from redis.asyncio import Redis


# WebSocket握手阶段的鉴权器
class RealtimeAuthenticator:
    # 初始化，注入JWT工具和redis连接
    def __init__(self, codec: JwtCodec, redis: Redis) -> None:
        self._codec = codec
        self._redis = redis

    # 校验前端传过来的access token，合法返回user_id，不合法返回None
    async def authenticate(self, token: str | None) -> int | None:
        # 没传token直接放行失败
        if not token:
            return None
        try:
            # 解码jwt拿到载荷信息
            claims = self._codec.decode(token)
            # 只允许访问令牌，别的令牌直接拒绝
            if claims.token_type not in {None, TokenType.ACCESS}:
                return None
            # 把sub转成用户id
            user_id = int(claims.sub)
        except ValueError:
            # 解析失败、类型不对，直接鉴权失败
            return None
        # 去redis查这个用户当前有效的access令牌
        current = await self._redis.get(f"access:token:{user_id}")
        # 用安全字符串对比，防止时序攻击；token对上才返回用户id
        return user_id if secrets.compare_digest(token, str(current) if current else "") else None
