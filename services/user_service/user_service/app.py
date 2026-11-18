"""UserService FastAPI application; workers and schedulers remain separate processes."""
# UserService 的 FastAPI 主入口文件
# 注意：接口服务、定时任务调度器是分开跑的独立进程，互不干扰
import time
# 用来记录请求耗时
from collections.abc import AsyncIterator
# 异步迭代器，给 lifespan 生命周期用
from contextlib import asynccontextmanager
# 用来写异步生命周期钩子（服务启动前、关闭后执行逻辑）
from typing import Any
# 任意类型，类型标注用

from fastapi import FastAPI, Request
# FastAPI：web框架核心；Request：拿到本次请求对象
from fastapi.exceptions import RequestValidationError
# 请求参数校验失败的异常（比如参数类型不对、少传参数）
from fastapi.responses import JSONResponse
from infinitechat.contracts import BaseResponse, ErrorCode
# 统一返回结构体、全局错误码

from infinitechat.foundation.context import bind_request_context
# 绑定请求上下文，链路追踪、日志里面带上请求ID

from infinitechat.foundation.security import JwtCodec, ServiceTokenCodec
# JwtCodec：普通用户JWT工具；ServiceTokenCodec：微服务之间调用的令牌工具

from infinitechat.foundation.telemetry import FoundationMetrics
# 监控指标工具，用来埋点统计接口耗时、请求次数

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
# prometheus 监控，对外暴露指标给监控系统抓取
from sqlalchemy import text
# 原生sql文本对象，用来写简单sql

from starlette.middleware.base import RequestResponseEndpoint
# 中间件用到的类型，用来接管请求到下一层
from starlette.responses import Response
# 基础响应对象

from user_service.config import UserServiceSettings
# 服务配置类
from user_service.dependencies import UserContainer
# 全局依赖容器，存放所有资源
from user_service.errors import BusinessError
# 业务自定义异常

from user_service.internal_api import InternalValidationService
# 内部校验业务服务

from user_service.internal_api.routes import router as internal_router
# 导入内部接口路由，别名 internal_router

from user_service.runtime import CoreRuntime, build_core_runtime
# 核心运行时、构建核心运行时的函数，专门组装数据库资源

USER_METRICS = FoundationMetrics.create(namespace="user_service")
# 创建监控指标实例，命名空间 user_service，监控平台用来区分不同服务的指标

# 构建依赖容器
async def build_container(settings: UserServiceSettings) -> tuple[UserContainer, CoreRuntime]:
    # 先构建数据库相关的核心运行时（引擎、会话工厂）
    core = build_core_runtime(settings)
    return (
        UserContainer(
            settings=settings,
            # 把核心运行时里的数据库引擎塞进去
            engine=core.engine,
            # 把核心运行时里的会话工厂塞进去
            session_factory=core.session_factory,
            # 实例化内部校验服务，给它传会话工厂
            internal=InternalValidationService(
                session_factory=core.session_factory,
            ),
            # 组装服务令牌工具：先用密钥生成JwtCodec，再包成ServiceTokenCodec
            service_tokens=ServiceTokenCodec(JwtCodec(settings.service_jwt_secret)),
        ),
        # 把core也返回，后面服务关闭的时候要用它执行close释放数据库连接
        core,
    )

# 创建应用
def create_app(
    # 可以外部提前传好容器，不传就内部自动新建
    container: UserContainer | None = None,
    *,  # 后面的参数必须用名字传，防止传参顺序搞混
    # 可以外部传入配置，不传就自动读取环境变量生成配置
    settings: UserServiceSettings | None = None,
) -> FastAPI:
    # 应用生命周期钩子：服务启动前、关闭后执行逻辑
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 标记这个函数自己创建的core，后面要负责关闭
        owned_core: CoreRuntime | None = None
        if container is not None:
            # 外部已经准备好了容器，直接挂载到app上
            app.state.container = container
        else:
            # 没传配置就自动新建配置（自动读环境变量）
            configured = settings or UserServiceSettings()  # type: ignore[call-arg]
            # 组装容器和数据库核心资源
            built_container, owned_core = await build_container(configured)
            # 挂载容器到app，接口里get_container就是拿这里
            app.state.container = built_container
        try:
            # 到这里：服务启动完成，开始接收请求
            yield
        finally:
            # 服务要关闭了，不管正常退出还是崩溃，都会走到这里
            if owned_core is not None:
                # 关闭数据库连接池，释放连接
                await owned_core.close()

    # 实例化FastAPI，绑定生命周期函数
    app = FastAPI(title="InfiniteChat UserService", version="0.1.0", lifespan=lifespan)
    # 把内部接口路由挂载上去
    app.include_router(internal_router)

    # 注册HTTP中间件，每一个进来的请求都会先走这个函数
    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # 记录请求开始时间，用来算耗时
        started = time.monotonic()
        # 默认标记本次请求成功
        outcome = "success"
        # 从请求头拿追踪ID，用来链路追踪
        request_id = request.headers.get("X-Request-Id")
        try:
            # 绑定追踪上下文，日志、链路都会带上这两个ID
            with bind_request_context(
                request_id=request_id,
                correlation_id=request.headers.get("X-Correlation-Id"),
            ) as context:
                # 把请求交给后面的路由、业务逻辑去处理
                response = await call_next(request)
                # 把追踪ID放回响应头，方便排查问题
                response.headers["X-Request-Id"] = context.request_id
                response.headers["X-Correlation-Id"] = context.correlation_id
                # 5xx服务端错误，标记结果为error
                if response.status_code >= 500:
                    outcome = "error"
                return response
        except Exception:
            # 代码抛出异常，标记为错误
            outcome = "error"
            # 异常继续往外抛，交给全局异常处理器处理返回报文
            raise
        finally:
            # 不管成功失败，都上报监控指标
            # 统计请求次数，带上服务名、请求类型、路径、成功/失败标记
            USER_METRICS.requests.labels("UserService", "http", request.url.path, outcome).inc()
            # 统计本次请求耗时
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
        async with active.session_factory() as session:  # 开一个数据库会话, 用完自动关闭
            await session.scalar(text("SELECT 1"))
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