"""FastAPI dependency boundary and application container."""
# 依赖注入容器 + 全局依赖函数
# 统一管理配置、数据库、业务服务、服务令牌工具，给路由直接取用
from dataclasses import dataclass

from fastapi import Header, Request
# Request：拿到app全局状态；Header：读取请求头里的服务令牌
from infinitechat.contracts import ErrorCode
# 全局错误码

from infinitechat.foundation.security import ServiceTokenCodec, TokenInvalidError
# 服务令牌校验工具、令牌异常

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
# 异步数据库引擎、会话工厂

from user_service.config import UserServiceSettings
# 用户服务配置类
from user_service.errors import BusinessError
# 项目业务异常

from user_service.internal_api import InternalValidationService
# 内部校验业务服务

# 依赖容器，所有单例资源都放这里
@dataclass(slots=True)
# slots=True节省内存，不允许随便新增属性
class UserContainer:
    settings: UserServiceSettings
    # 项目配置
    engine: AsyncEngine
    # 数据库异步引擎
    session_factory: async_sessionmaker[AsyncSession]
    # 异步会话工厂，用来生成数据库会话
    internal: InternalValidationService
    # 内部校验服务实例
    service_tokens: ServiceTokenCodec
    # 服务间JWT令牌工具

# 获取容器依赖，从app.state里取出提前装好的container
def get_container(request: Request) -> UserContainer:
    container: UserContainer = request.app.state.container
    return container

# 内部服务鉴权依赖：校验X-Service-Token头
def require_internal_service(
    request: Request,
    # 从请求头 X-Service-Token 读取令牌，不传就是None
    service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> str:
    container = get_container(request)
    # 把配置里逗号分隔的白名单字符串转成不可变集合，自动去空格、过滤空值
    allowed = frozenset(
        caller.strip()
        for caller in container.settings.internal_allowed_callers.split(",")
        if caller.strip()
    )
    try:
        # 校验令牌，同时检查调用方是否在白名单
        identity = container.service_tokens.verify(
            service_token,
            allowed_callers=allowed,
        )
    except (TokenInvalidError, ValueError) as error:
        # 令牌非法、不在白名单，抛出无权限业务异常
        raise BusinessError(ErrorCode.NO_AUTH_ERROR) from error
    # 校验通过，返回调用服务名称，路由里可以拿到是谁在调用
    return identity.service_name