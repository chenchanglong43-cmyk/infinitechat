"""UserService FastAPI application; workers and schedulers remain separate processes."""

import asyncio  # 异步编程
import time  # 时间
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from infinitechat.contracts import BaseResponse, ErrorCode

from infinitechat.foundation.context import bind_request_context

from infinitechat.foundation.discovery import ServiceInstance, create_nacos_registry
from infinitechat.foundation.ids import SnowflakeNodeLease

from infinitechat.foundation.security import JwtCodec, ServiceTokenCodec
from infinitechat.foundation.settings import NacosSettings

from infinitechat.foundation.telemetry import FoundationMetrics

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from user_service.auth import AuthService, PasswordManager
from user_service.auth.routes import router as auth_router
from user_service.config import UserServiceSettings
from user_service.contacts import ContactService

from user_service.contacts.routes import router as contacts_router
from user_service.dependencies import UserContainer
from user_service.errors import BusinessError
from user_service.groups import GroupService

from user_service.groups.routes import router as groups_router

from user_service.integrations.email import SmtpEmailSender

from user_service.integrations.minio import AvatarStorage

from user_service.integrations.realtime_locator import RealtimeLocator

from user_service.internal_api import InternalValidationService

from user_service.internal_api.routes import router as internal_router

from user_service.runtime import CoreRuntime, build_core_runtime
from user_service.users import UserProfileService
from user_service.users.routes import router as users_router

USER_METRICS = FoundationMetrics.create(namespace="user_service")

# 构建依赖容器
async def build_container(settings: UserServiceSettings) -> tuple[UserContainer, CoreRuntime]:
    core = build_core_runtime(settings)  # 构建核心运行时实例，包含数据库引擎、缓存、ID生成器等基础组件
    registry = await create_nacos_registry(  # 创建Nacos注册中心客户端
        NacosSettings(  # 组装Nacos配置对象
            nacos_server_addr=settings.nacos_server_addr,  # Nacos服务地址
            nacos_namespace=settings.nacos_namespace,  # Nacos命名空间
            nacos_username=settings.nacos_username,  # Nacos账号
            nacos_password=settings.nacos_password,  # Nacos密码
            nacos_cache_dir=Path(settings.nacos_cache_dir),  # Nacos缓存目录，转为Path对象
            nacos_log_dir=Path(settings.nacos_log_dir),  # Nacos日志目录，转为Path对象
        )
    )
    instance = ServiceInstance(  # 构造当前服务注册实例信息
        service_name=settings.service_name,  # 服务名称
        ip=settings.service_advertise_host,  # 对外暴露的主机IP
        port=settings.service_port,  # 服务端口
        metadata={  # 注册元数据
            "version": "0.1.0",  # "version": "0.1.0"
            "protocol": "http",  # "protocol": "http"
            "httpPort": str(settings.service_port),  # "httpPort": 转成字符串
        },
    )
    await registry.register(instance)  # 将当前服务实例注册到Nacos
    jwt_codec = JwtCodec(  # 创建用户侧JWT编解码器
        settings.jwt_secret,
        key_encoding=settings.jwt_key_encoding,
    )
    locator = RealtimeLocator(registry)  # 实例化实时长连接定位器，依赖Nacos注册中心
    return (  # 返回元组：依赖容器 + 核心运行时
        UserContainer(  # 组装全局依赖容器
            settings=settings,  # 注入服务配置
            engine=core.engine,  # 注入数据库引擎
            session_factory=core.session_factory,  # 注入数据库会话工厂
            cache=core.cache,  # 注入Redis缓存实例
            registry=registry,  # 注入Nacos注册客户端
            registered_instance=instance,  # 注入已注册的服务实例
            auth=AuthService(  # 实例化认证服务
                session_factory=core.session_factory,
                cache=core.cache,
                email_sender=SmtpEmailSender(settings),  # 注入邮件发送器
                realtime_locator=locator,  # 注入长连接定位器
                # password_manager: 创建密码管理器
                password_manager=PasswordManager(legacy_salt=settings.legacy_password_salt),
                jwt_codec=jwt_codec,  # 注入JWT编解码器
                id_generator=core.ids,  # 注入ID生成器
                settings=settings,
            ),
            contacts=ContactService(  # 实例化联系人服务
                session_factory=core.session_factory,
                cache=core.cache,
                id_generator=core.ids,
            ),
            groups=GroupService(  # 实例化群组服务
                session_factory=core.session_factory,
                id_generator=core.ids,
                settings=settings,
            ),
            profiles=UserProfileService(  # 实例化用户资料服务
                session_factory=core.session_factory,
                avatar_storage=AvatarStorage(settings),  # 注入Minio头像存储
                allowed_avatar_hosts=frozenset(  # 头像允许的域名白名单，不可变集合
                    {
                        settings.minio_endpoint.rsplit(":", maxsplit=1)[0],  # 截取Minio地址，去掉端口
                        urlsplit(settings.public_gateway_base_url).hostname or "",  # 解析网关域名
                    }
                ),
            ),
            internal=InternalValidationService(  # 实例化内部校验服务
                session_factory=core.session_factory,
                cache=core.cache,
            ),
            # service_tokens(服务令牌): 创建服务 Token 编解码
            service_tokens=ServiceTokenCodec(JwtCodec(settings.service_jwt_secret)),
        ),
        core,
    )

# 创建应用
# 创建应用
def create_app(
    container: UserContainer | None = None,
    *,  # 星号后面的参数只能按名字传
    settings: UserServiceSettings | None = None,
) -> FastAPI:
    # 应用生命周期
    @asynccontextmanager  # 把下面的函数变成异步上下文管理器
    # 应用生命周期
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned_core: CoreRuntime | None = None  # owned核心先设为空, 类型是核心运行时(可为空)
        lease: SnowflakeNodeLease | None = None  # 租约先设为空, 类型是雪花节点租约(可为空)
        lease_stop: asyncio.Event | None = None  # 租约停止先设为空, 类型是事件(可为空)
        lease_task: asyncio.Task[None] | None = None  # 租约任务先设为空, 类型是任务(可为空)
        if container is not None:
            app.state.container = container
        else:
            # 配置: 配置 或者 创建用户服务配置
            configured = settings or UserServiceSettings()  # type: ignore[call-arg]
            # 拆成 built依赖容器, owned核心: 等构建依赖容器
            built_container, owned_core = await build_container(configured)
            app.state.container = built_container
            lease = SnowflakeNodeLease(
                owned_core.cache.lease_store,
                data_center_id=configured.snowflake_data_center_id,
                worker_id=configured.snowflake_worker_id,
                owner_label=f"{configured.service_name}:api",
            )
            await lease.acquire()
            lease_stop = asyncio.Event()
            lease_task = asyncio.create_task(lease.maintain(lease_stop))
            app.state.snowflake_lease = lease
        try:  # 下面这段可能出错, 出错交给 except 处理
            yield  # 交出
        finally:  # 不管成功还是出错, 最后都执行这里
            if owned_core is not None:
                # 断言 租约 不为空 并且 租约停止 不为空 并且 租约任务 不为空, 不成立就报错
                assert lease is not None and lease_stop is not None and lease_task is not None
                lease_stop.set()  # 把租约停止设成
                await lease_task
                await lease.release()
                # 有效的(用户依赖容器): 应用的状态的依赖容器
                active: UserContainer = app.state.container
                await active.registry.deregister(active.registered_instance)
                await active.registry.close()
                await owned_core.close()

    app = FastAPI(title="InfiniteChat UserService", version="0.1.0", lifespan=lifespan)
    app.include_router(auth_router)  # 挂上路由组
    app.include_router(users_router)  # 挂上路由组
    app.include_router(contacts_router)  # 挂上路由组
    app.include_router(groups_router)  # 挂上路由组
    app.include_router(internal_router)  # 挂上路由组

    # 请求上下文
    @app.middleware("http")  # 注册中间件: 每个请求都会先经过下面这个函数
    # 请求上下文
    async def request_context(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.monotonic()
        outcome = "success"
        request_id = request.headers.get("X-Request-Id")
        try:  # 下面这段可能出错, 出错交给 except 处理
            with bind_request_context(  # 绑定请求上下文 context, 用完自动收尾
                request_id=request_id,
                correlation_id=request.headers.get("X-Correlation-Id"),
            ) as context:
                response = await call_next(request)
                response.headers["X-Request-Id"] = context.request_id
                response.headers["X-Correlation-Id"] = context.correlation_id
                if response.status_code >= 500:
                    outcome = "error"
                return response
        except Exception:  # 任何异常都在这里接住
            outcome = "error"
            raise  # 把刚才的异常原样抛出去
        finally:  # 不管成功还是出错, 最后都执行这里
            # 它加 1
            USER_METRICS.requests.labels("UserService", "http", request.url.path, outcome).inc()
            # 它记录一次 取单调时钟 - 开始时间
            USER_METRICS.request_duration.labels("UserService", "http", request.url.path).observe(
                time.monotonic() - started
            )

    # 业务错误处理
    @app.exception_handler(BusinessError)  # business错误 统一交给下面的函数处理
    # 业务错误处理
    async def business_error(request: Request, error: BusinessError) -> JSONResponse:
        body = BaseResponse[Any].error(error.code, message=error.message)
        status_code = (
            401
            if request.url.path.startswith("/api/internal/")
            and error.code
            in {  # 在下面这些值里
                ErrorCode.NOT_LOGIN_ERROR,
                ErrorCode.NO_AUTH_ERROR,
                ErrorCode.TOKEN_MISSING,
                ErrorCode.TOKEN_EXPIRED,
                ErrorCode.TOKEN_INVALID,
            }
            else 200
        )
        return JSONResponse(status_code=status_code, content=body.to_wire())

    @app.exception_handler(RequestValidationError)  # 请求校验错误 统一交给下面的函数处理
    # 参数校验错误
    async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        # 第一: 调用 errors(错误) 时取 调用 errors(错误)[0], 否则取 1 个键的字典
        first = error.errors()[0] if error.errors() else {"msg": "参数校验失败"}
        body = BaseResponse[Any].error(
            ErrorCode.INVALID_PARAMETER_ERROR,
            message=str(first.get("msg", "参数校验失败")),
        )
        return JSONResponse(status_code=200, content=body.to_wire())

    # 兜底错误处理: 上面两种以外的异常也回 200 和统一响应
    @app.exception_handler(Exception)  # 其余异常 统一交给下面的函数处理
    # 兜底错误处理
    async def unexpected_error(request: Request, _error: Exception) -> JSONResponse:
        if request.url.path.startswith("/health/"):
            # 探针照旧回 500, 编排工具才知道服务没就绪
            return JSONResponse(status_code=500, content={"status": "DOWN"})
        # 联系人和群组接口用错误码自带的文案, 其余接口是 "系统错误"
        builtin = request.url.path.startswith(("/api/contact/", "/api/group"))
        body = BaseResponse[Any].error(
            ErrorCode.SYSTEM_ERROR,
            message=None if builtin else "系统错误",
        )
        return JSONResponse(status_code=200, content=body.to_wire())

    # 存活探针
    @app.get("/health/live")  # 注册 GET 接口 /health/live
    # 存活探针
    async def live() -> dict[str, str]:
        return {"status": "UP"}

    # 就绪探针
    @app.get("/health/ready")  # 注册 GET 接口 /health/ready
    # 就绪探针
    async def ready(request: Request) -> dict[str, str]:
        active = request.app.state.container
        lease = getattr(request.app.state, "snowflake_lease", None)
        if lease is not None and not lease.held:
            # Snowflake node lease lost
            raise RuntimeError("Snowflake node lease lost")
        async with active.session_factory() as session:  # 开一个数据库会话, 用完自动关闭
            await session.scalar(text("SELECT 1"))
        if not await active.cache.ping():
            # Redis readiness failed
            raise RuntimeError("Redis readiness failed")
        return {"status": "UP"}

    # 指标导出
    @app.get("/metrics")  # 注册 GET 接口 /metrics
    # 指标导出
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app

# 默认应用入口
def create_default_app() -> FastAPI:
    return create_app()