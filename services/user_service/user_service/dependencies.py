"""FastAPI dependency boundary and application container."""

from dataclasses import dataclass

from fastapi import Header, Request
from infinitechat.contracts import ErrorCode

from infinitechat.foundation.discovery import NacosRegistry, ServiceInstance

from infinitechat.foundation.security import ServiceTokenCodec, TokenInvalidError

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from user_service.auth import AuthService
from user_service.config import UserServiceSettings
from user_service.contacts import ContactService
from user_service.errors import BusinessError
from user_service.groups import GroupService

from user_service.integrations.redis import UserRedis

from user_service.internal_api import InternalValidationService
from user_service.users import UserProfileService

# 依赖容器
@dataclass(slots=True)  # 数据类: 自动生成构造函数等方法, 省内存
# 依赖容器
class UserContainer:
    settings: UserServiceSettings  # 配置：用户服务配置
    engine: AsyncEngine  # 数据库引擎：异步数据库引擎
    # 会话工厂：异步会话工厂
    session_factory: async_sessionmaker[AsyncSession]
    cache: UserRedis  # 缓存：用户Redis
    registry: NacosRegistry  # 注册中心：Nacos注册中心
    registered_instance: ServiceInstance  # 字段 registered_instance: 服务实例
    auth: AuthService  # 鉴权：鉴权服务
    contacts: ContactService  # 联系人：联系人服务
    groups: GroupService  # 群：群服务
    profiles: UserProfileService  # 字段 profiles: 用户资料服务
    internal: InternalValidationService  # 内部：内部校验服务
    service_tokens: ServiceTokenCodec  # 服务令牌：服务令牌编解码器

# 获取容器
def get_container(request: Request) -> UserContainer:
    # 依赖容器(用户依赖容器): 请求的应用的状态的依赖容器
    container: UserContainer = request.app.state.container
    return container

# 当前用户ID
async def current_user_id(
    request: Request,
    access_token: str | None = Header(default=None, alias="Access-Token"),
) -> int:
    return await get_container(request).auth.authenticate_access_token(access_token)

# 校验同一用户
def require_same_user(authenticated_user_id: int, requested_user_id: int) -> None:
    if authenticated_user_id != requested_user_id:
        raise BusinessError(ErrorCode.TOKEN_MISMATCH)

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

# 当前用户, 或者带服务令牌的内部调用方(这时没有用户, 返回 None); 会话接收者接口两种都认
async def current_user_or_internal(
    request: Request,
    access_token: str | None = Header(default=None, alias="Access-Token"),
    service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> int | None:
    if service_token is not None:
        require_internal_service(request, service_token)  # 校验内部服务
        return None
    return await current_user_id(request, access_token)