"""UserService FastAPI application; workers and schedulers remain separate processes."""  # 模块文档字符串：用户服务FastAPI应用，任务worker和调度器为独立进程

import time  # 时间模块
from collections.abc import AsyncIterator  # 导入异步迭代器类型
from contextlib import asynccontextmanager  # 异步上下文管理器，用于生命周期钩子
from pathlib import Path  # 路径工具类
from typing import Any  # 任意类型注解
from urllib.parse import urlsplit  # url拆分工具

from fastapi import FastAPI, Request  # 导入FastAPI主类、请求对象
from fastapi.exceptions import RequestValidationError  # FastAPI请求参数校验异常
from fastapi.responses import JSONResponse  # JSON返回响应

from infinitechat.contracts import BaseResponse, ErrorCode  # 导入统一返回体、错误码枚举

from infinitechat.foundation.context import bind_request_context  # 请求上下文绑定工具

from infinitechat.foundation.discovery import ServiceInstance, create_nacos_registry  # 服务实例、Nacos注册中心创建方法

from infinitechat.foundation.security import JwtCodec, ServiceTokenCodec  # JWT编解码器、内部服务令牌编解码器
from infinitechat.foundation.settings import NacosSettings  # Nacos配置结构体

from infinitechat.foundation.telemetry import FoundationMetrics  # 基础指标采集工具

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # prometheus指标常量、指标内容生成函数
from sqlalchemy import text  # SQLAlchemy原生SQL文本构造

from starlette.middleware.base import RequestResponseEndpoint  # 中间件请求处理端点类型
from starlette.responses import Response  # 基础响应对象

from user_service.auth import AuthService, PasswordManager  # 认证服务、密码管理器
from user_service.auth.routes import router as auth_router  # 认证模块路由
from user_service.config import UserServiceSettings  # 用户服务配置类
from user_service.contacts import ContactService  # 联系人服务

from user_service.contacts.routes import router as contacts_router  # 联系人模块路由
from user_service.dependencies import UserContainer  # 用户服务依赖容器
from user_service.errors import BusinessError  # 业务自定义异常
from user_service.groups import GroupService  # 群组服务

from user_service.groups.routes import router as groups_router  # 群组模块路由

from user_service.integrations.email import SmtpEmailSender  # SMTP邮件发送器

from user_service.integrations.minio import AvatarStorage  # Minio头像存储封装

from user_service.integrations.realtime_locator import RealtimeLocator  # 实时长连接地址定位器

from user_service.internal_api import InternalValidationService  # 内部权限校验服务

from user_service.internal_api.routes import router as internal_router  # 内部接口路由

from user_service.runtime import CoreRuntime, build_core_runtime  # 核心运行时、构建运行时实例函数
from user_service.users import UserProfileService  # 用户资料服务
from user_service.users.routes import router as users_router  # 用户资料模块路由

USER_METRICS = FoundationMetrics.create(namespace="user_service")  # 创建用户服务专属指标采集实例，命名空间user_service


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
        if container is not None:  # 如果外部传入了依赖容器
            app.state.container = container  # 挂载到app状态上
        else:
            # 配置: 配置 或者 创建用户服务配置
            configured = settings or UserServiceSettings()  # type: ignore[call-arg]
            # 拆成 built依赖容器, owned核心: 等构建依赖容器
            built_container, owned_core = await build_container(configured)
            app.state.container = built_container  # 挂载新建好的容器
        try:  # 下面这段可能出错, 出错交给 except 处理
            yield  # 交出控制权，服务在此期间运行
        finally:  # 不管成功还是出错, 最后都执行这里
            if owned_core is not None:  # 是当前lifespan自己创建的核心运行时才需要关闭
                # 有效的(用户依赖容器): 应用的状态的依赖容器
                active: UserContainer = app.state.container
                await active.registry.deregister(active.registered_instance)  # Nacos反注册
                await active.registry.close()  # 关闭注册中心客户端
                await owned_core.close()  # 关闭数据库、缓存等底层资源

    app = FastAPI(title="InfiniteChat UserService", version="0.1.0", lifespan=lifespan)  # 实例化FastAPI应用，绑定生命周期
    app.include_router(auth_router)  # 挂上认证路由组
    app.include_router(users_router)  # 挂上用户资料路由组
    app.include_router(contacts_router)  # 挂上联系人路由组
    app.include_router(groups_router)  # 挂上群组路由组
    app.include_router(internal_router)  # 挂上内部校验路由组

    # 请求上下文
    @app.middleware("http")  # 注册中间件: 每个请求都会先经过下面这个函数
    # 请求上下文
    async def request_context(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.monotonic()  # 记录请求开始时间，单调时钟不受系统时间影响
        outcome = "success"  # 请求结果标记，默认成功
        request_id = request.headers.get("X-Request-Id")  # 从请求头获取请求ID
        try:  # 下面这段可能出错, 出错交给 except 处理
            with bind_request_context(  # 绑定请求上下文 context, 用完自动收尾
                request_id=request_id,
                correlation_id=request.headers.get("X-Correlation-Id"),
            ) as context:
                response = await call_next(request)  # 放行请求，执行后续逻辑
                response.headers["X-Request-Id"] = context.request_id  # 回写请求ID到响应头
                response.headers["X-Correlation-Id"] = context.correlation_id  # 回写链路追踪ID
                if response.status_code >= 500:  # 5xx服务端错误标记为失败
                    outcome = "error"
                return response
        except Exception:  # 任何异常都在这里接住
            outcome = "error"  # 标记结果为失败
            raise  # 把刚才的异常原样抛出去，交给异常处理器
        finally:  # 不管成功还是出错, 最后都执行这里
            # 请求计数指标 +1
            USER_METRICS.requests.labels("UserService", "http", request.url.path, outcome).inc()
            # 记录请求耗时
            USER_METRICS.request_duration.labels("UserService", "http", request.url.path).observe(
                time.monotonic() - started
            )

    # 业务错误处理
    @app.exception_handler(BusinessError)  # business错误 统一交给下面的函数处理
    # 业务错误处理
    async def business_error(request: Request, error: BusinessError) -> JSONResponse:
        body = BaseResponse[Any].error(error.code, message=error.message)  # 组装统一错误返回体
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
        return JSONResponse(status_code=status_code, content=body.to_wire())  # 返回JSON响应

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
        return {"status": "UP"}  # 存活，容器不重启

    # 就绪探针
    @app.get("/health/ready")  # 注册 GET 接口 /health/ready
    # 就绪探针
    async def ready(request: Request) -> dict[str, str]:
        active = request.app.state.container
        async with active.session_factory() as session:  # 开一个数据库会话, 用完自动关闭
            await session.scalar(text("SELECT 1"))  # 探活数据库
        if not await active.cache.ping():
            # Redis readiness failed
            raise RuntimeError("Redis readiness failed")
        return {"status": "UP"}  # 全部依赖正常，服务就绪

    # 指标导出
    @app.get("/metrics")  # 注册 GET 接口 /metrics
    # 指标导出
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)  # 返回prometheus指标文本

    return app

# 默认应用入口
def create_default_app() -> FastAPI:
    return create_app()  # 无参创建默认应用实例