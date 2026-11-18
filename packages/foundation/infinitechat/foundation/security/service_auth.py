"""Short-lived service JWTs and caller allow-list validation."""

from dataclasses import dataclass
from datetime import timedelta

from infinitechat.foundation.security.jwt import JwtCodec, TokenInvalidError, TokenType

# 服务身份
@dataclass(frozen=True, slots=True)  # 数据类: 自动生成构造函数等方法, 不可修改, 省内存
# 服务身份
class ServiceIdentity:
    service_name: str  # 服务名

# 服务 Token 编解码
class ServiceTokenCodec:
    # 初始化
    # 构造方法: 创建对象时把依赖存起来
    def __init__(self, codec: JwtCodec, *, lifetime: timedelta = timedelta(minutes=5)) -> None:
        if lifetime <= timedelta(0):
            # service token lifetime must be positive
            raise ValueError("service token lifetime must be positive")
        self._codec = codec
        self._lifetime = lifetime

    # 签发服务 Token
    def issue(self, identity: ServiceIdentity) -> str:
        return self._codec.encode(
            subject=identity.service_name,
            expires_in=self._lifetime,
            token_type=TokenType.SERVICE,
        )

    # 校验服务 Token
    def verify(self, token: str | None, *, allowed_callers: frozenset[str]) -> ServiceIdentity:
        claims = self._codec.decode(token, expected_type=TokenType.SERVICE)
        if claims.sub not in allowed_callers:
            # service caller is not allowed
            raise TokenInvalidError("service caller is not allowed")
        return ServiceIdentity(service_name=claims.sub)