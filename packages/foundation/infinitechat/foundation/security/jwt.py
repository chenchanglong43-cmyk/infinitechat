"""HS256 JWT compatibility codec with stable, non-leaking exceptions."""
# JWT 工具模块，只用HS256算法
# 异常做了封装，不会把密钥、原始token信息抛出去，防止信息泄露

import base64
# base64：用来做base64编解码，JWT内部会用到
import binascii
# binascii：二进制和字符串转换工具，处理编码异常
from collections.abc import Mapping
# Mapping：代表字典这类键值映射类型，用来接收payload
from datetime import UTC, datetime, timedelta
# UTC：标准UTC时区；datetime：时间对象；timedelta：用来算时间差、过期时间
from enum import StrEnum
# StrEnum：字符串枚举，枚举值本身就是字符串，方便序列化
from typing import Any
# Any：任意类型，不确定类型的时候用

import jwt
# PyJWT库，核心JWT加解密工具

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
# BaseModel：Pydantic数据模型，用来校验token里的载荷
# ConfigDict：给模型配置参数
# Field：给字段加额外配置、别名、默认值
# SecretStr：密钥专用类型，打印的时候不会明文输出，保护密钥
# ValidationError：Pydantic校验失败抛出的异常

# Token类型枚举
class TokenType(StrEnum):
    # 访问令牌，接口鉴权用，有效期短
    ACCESS = "access"
    # 刷新令牌，用来换新access token，有效期更长
    REFRESH = "refresh"
    # 服务间调用令牌，微服务内部通信用
    SERVICE = "service"

# Token载荷结构体，解析出来的内容会装进这个模型校验
class TokenClaims(BaseModel):
    model_config = ConfigDict(extra="allow")
    # extra="allow"：允许载荷里带额外字段，不会报错，方便以后扩展

    sub: str
    # sub：主体，一般放用户id或者服务id
    iat: int
    # iat：签发时间，时间戳，代表token什么时候生成的
    exp: int
    # exp：过期时间，时间戳，超过这个时间token就失效
    token_type: TokenType | None = Field(default=None, alias="token_type")
    # token_type：令牌类型，区分access/refresh/service，别名和字段名一致

# Token错误的父类
class TokenError(ValueError):
    """Base token error that deliberately omits token and key material."""
    # 所有token相关异常都继承它
    # 重点：异常信息里不会打印原始token、密钥，避免日志泄露敏感信息

# 没有传token
class TokenMissingError(TokenError):
    pass

# token已经过期
class TokenExpiredError(TokenError):
    pass

# token格式错误、签名不对、校验失败都归这个
class TokenInvalidError(TokenError):
    pass

# JWT 编解码工具类，封装HS256的签发逻辑
class JwtCodec:
    # 构造函数：创建实例的时候把密钥、校验配置一次性装好
    def __init__(
        self,
        secret: SecretStr | str,
        *,
        key_encoding: str = "raw",
        issuer: str | None = None,
        audience: str | None = None,
        leeway_seconds: int = 0,
    ) -> None:
        # 兼容两种密钥类型：SecretStr是安全类型，不会明文打印；普通字符串直接拿
        raw_secret = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        # HS256要求密钥至少32个字符，太短不安全，直接拦截
        if len(raw_secret) < 32:
            raise ValueError("HS256 secret must contain at least 32 characters")

        # 密钥是base64编码的情况，先解码拿到原始字节
        if key_encoding == "base64":
            try:
                decoded_key = base64.b64decode(raw_secret, validate=True)
            except (binascii.Error, ValueError) as error:
                # base64串格式不对就抛异常，带上原始异常方便排查
                raise ValueError("JWT base64 secret is invalid") from error
            # 解码后的原始密钥字节也不能少于32字节
            if len(decoded_key) < 32:
                raise ValueError("decoded HS256 key must contain at least 32 bytes")
            self._key: str | bytes = decoded_key
        # raw模式：密钥就是原始字符串，不用解码
        elif key_encoding == "raw":
            self._key = raw_secret
        else:
            # 只允许raw或者base64两种密钥格式
            raise ValueError("key_encoding must be raw or base64")

        # 保存签发者、受众、时间容错偏移量，后面解码校验用
        self._issuer = issuer
        self._audience = audience
        self._leeway_seconds = leeway_seconds

    # 签发token，生成JWT字符串
    def encode(
        self,
        *,
        subject: str,
        expires_in: timedelta,
        token_type: TokenType | None = None,
        additional_claims: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> str:
        # sub不能为空，一般放用户id/服务id
        if not subject:
            raise ValueError("JWT subject must not be empty")
        # 过期时长必须大于0，不能生成已经过期的token
        if expires_in <= timedelta(0):
            raise ValueError("JWT expiration must be positive")

        # 取当前UTC时间；支持传入now，方便单元测试固定时间
        issued_at = (now or datetime.now(UTC)).astimezone(UTC)
        # 基础载荷：主体、签发时间、过期时间
        payload: dict[str, Any] = {
            "sub": subject,
            "iat": issued_at,
            "exp": issued_at + expires_in,
        }

        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        # 带上token类型，区分access/refresh/service
        if token_type is not None:
            payload["token_type"] = token_type.value

        # 处理额外自定义字段
        if additional_claims:
            # 保留字段，不允许外部覆盖，防止冲突
            reserved = {"sub", "iat", "exp", "iss", "aud", "token_type"}
            if reserved.intersection(additional_claims):
                raise ValueError("additional claims must not override reserved JWT claims")
            payload.update(additional_claims)

        # 使用HS256算法签名，返回最终token字符串
        return jwt.encode(payload, self._key, algorithm="HS256")

    # 校验并解析 Token
    def decode(
        self,
        token: str | None,
        *,
        expected_type: TokenType | None = None,
    ) -> TokenClaims:
        # 没传token直接抛“token缺失”异常
        if not token:
            raise TokenMissingError("token is missing")
        try:
            # PyJWT库做核心校验：验签名、过期时间、签发者、受众
            payload = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={
                    # 强制要求载荷里必须有 sub、iat、exp 这三个字段
                    "require": ["sub", "iat", "exp"],
                    # 只有配置了issuer，才去校验签发者；没配置就跳过校验
                    "verify_iss": self._issuer is not None,
                    # 只有配置了audience，才去校验受众；没配置就跳过校验
                    "verify_aud": self._audience is not None,
                },
            )
            # 用Pydantic模型校验载荷结构，自动转成TokenClaims对象
            claims = TokenClaims.model_validate(payload)
        except jwt.ExpiredSignatureError as error:
            # 捕获token过期异常，包装成我们自定义的异常，不泄露内部信息
            raise TokenExpiredError("token is expired") from error
        except (jwt.InvalidTokenError, ValidationError) as error:
            # 签名错误、格式乱、字段校验失败，统一归为token无效
            raise TokenInvalidError("token is invalid") from error
        if expected_type is not None and claims.token_type is not expected_type:
            raise TokenInvalidError("token type is invalid")
        # 全部校验通过，返回解析好的载荷对象
        return claims