"""JWT and service-to-service authentication helpers."""

from infinitechat.foundation.security.jwt import (
    JwtCodec,  # JWT 编解码器
    TokenClaims,  # 解码后的载荷
    TokenError,  # 令牌异常父类
    TokenExpiredError,  # 令牌过期
    TokenInvalidError,  # 令牌无效
    TokenMissingError,  # 令牌缺失
    TokenType,  # 令牌类型
)
from infinitechat.foundation.security.service_auth import ServiceIdentity, ServiceTokenCodec

__all__ = (  # 对外导出的公共 API
    "JwtCodec",
    "ServiceIdentity",
    "ServiceTokenCodec",
    "TokenClaims",
    "TokenError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TokenMissingError",
    "TokenType",
)