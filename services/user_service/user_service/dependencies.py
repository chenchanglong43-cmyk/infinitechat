"""FastAPI dependency boundary and application container."""

from dataclasses import dataclass

from fastapi import Header, Request
from infinitechat.contracts import ErrorCode

from infinitechat.foundation.security import ServiceTokenCodec, TokenInvalidError

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from user_service.config import UserServiceSettings
from user_service.errors import BusinessError

from user_service.integrations.redis import UserRedis

from user_service.internal_api import InternalValidationService

# 依赖容器
@dataclass(slots=True)  # 数据类: 自动生成构造函数等方法, 省内存
# 依赖容器
class UserContainer:
    settings: UserServiceSettings  # 配置：用户服务配置
    engine: AsyncEngine  # 数据库引擎：异步数据库引擎
    # 会话工厂：异步会话工厂
    session_factory: async_sessionmaker[AsyncSession]
    cache: UserRedis  # 缓存：用户Redis
    internal: InternalValidationService  # 内部：内部校验服务
    service_tokens: ServiceTokenCodec  # 服务令牌：服务令牌编解码器

# 获取容器
def get_container(request: Request) -> UserContainer:
    # 依赖容器(用户依赖容器): 请求的应用的状态的依赖容器
    container: UserContainer = request.app.state.container
    return container

# 校验内部服务
def require_internal_service(
    request: Request,
    service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> str:
    container = get_container(request)
    allowed = frozenset(
        caller.strip()  # caller去掉两端空白
        for caller in container.settings.internal_allowed_callers.split(",")
        if caller.strip()
    )
    try:  # 下面这段可能出错, 出错交给 except 处理
        identity = container.service_tokens.verify(
            service_token,
            allowed_callers=allowed,
        )
    except (TokenInvalidError, ValueError) as error:
        raise BusinessError(ErrorCode.NO_AUTH_ERROR) from error
    return identity.service_name
