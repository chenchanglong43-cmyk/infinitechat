"""FastAPI async reverse proxy with complete P0 routing and authentication."""
# FastAPI异步反向代理网关，包含P0路由、鉴权完整能力

import time  # 导入时间模块，用于埋点计时
from collections.abc import AsyncIterator  # 导入异步迭代器类型注解
from contextlib import asynccontextmanager  # 导入异步上下文管理器，用于FastAPI生命周期
from dataclasses import dataclass  # 导入dataclass，定义依赖容器
from pathlib import Path  # 导入路径工具类，处理本地目录

import httpx  # 导入异步HTTP客户端库
from fastapi import FastAPI, Request  # 导入FastAPI框架核心、请求对象
from fastapi.middleware.cors import CORSMiddleware  # 导入CORS跨域中间件
from fastapi.responses import JSONResponse  # 导入JSON返回响应

from infinitechat.contracts import BaseResponse, ErrorCode, GatewayAuthErrorResponse  # 导入统一返回体、错误码、鉴权异常返回结构

from infinitechat.foundation.context import bind_request_context, current_request_context  # 导入请求上下文绑定工具

from infinitechat.foundation.discovery import NacosRegistry, ServiceInstance, create_nacos_registry  # 导入Nacos注册中心、服务实例、创建注册中心方法
from infinitechat.foundation.security import JwtCodec  # 导入JWT编解码器
from infinitechat.foundation.settings import NacosSettings  # 导入Nacos配置实体

from infinitechat.foundation.telemetry import FoundationMetrics  # 导入监控指标工具

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # 导入Prometheus指标导出工具
from redis.asyncio import Redis  # 导入异步Redis客户端

from starlette.middleware.base import RequestResponseEndpoint  # 导入Starlette中间件回调类型
from starlette.requests import ClientDisconnect  # 导入客户端断开异常
from starlette.responses import Response  # 导入基础响应类

from gateway.auth import GatewayAuthenticator, GatewayAuthError  # 导入网关认证器、认证异常
from gateway.config import GatewaySettings  # 导入网关配置实体
from gateway.proxy import ReverseProxy  # 导入反向代理类
from gateway.routing import (
    ROUTE_TARGETS,
    NacosUpstreamResolver,
    is_public_auth_exempt,
    target_for_path,
)  # 导入路由映射、上游解析器、免鉴权判断、路由匹配函数

GATEWAY_METRICS = FoundationMetrics.create(namespace="gateway")  # 创建网关命名空间的监控指标实例

# 网关依赖容器
@dataclass(slots=True)  # 定义数据类，开启slots节省内存开销
class GatewayContainer:
    settings: GatewaySettings  # 网关配置对象
    redis: Redis  # Redis异步连接实例
    registry: NacosRegistry  # Nacos注册中心实例
    registered_instance: ServiceInstance  # 当前网关注册到Nacos的服务实例信息
    http_client: httpx.AsyncClient  # 全局httpx异步客户端
    authenticator: GatewayAuthenticator  # 网关鉴权器实例
    resolver: NacosUpstreamResolver  # Nacos上游地址解析器
    proxy: ReverseProxy  # 反向代理实例

# 构建网关所有依赖组件
async def build_container(settings: GatewaySettings) -> GatewayContainer:
    # 初始化Redis异步连接
    redis = Redis.from_url(
        settings.redis_url,  # 读取配置里的Redis连接地址
        db=settings.redis_db,  # 使用配置指定的Redis库号
        decode_responses=True,  # 自动把返回字节解码为字符串
        protocol=2,  # 使用Redis RESP2协议
    )
    # 初始化Nacos注册中心
    registry = await create_nacos_registry(
        NacosSettings(
            nacos_server_addr=settings.nacos_server_addr,  # Nacos服务地址
            nacos_namespace=settings.nacos_namespace,  # Nacos命名空间
            nacos_username=settings.nacos_username,  # Nacos账号
            nacos_password=settings.nacos_password,  # Nacos密码
            nacos_cache_dir=Path(settings.nacos_cache_dir),  # Nacos缓存目录，转为Path对象
            nacos_log_dir=Path(settings.nacos_log_dir),  # Nacos日志目录，转为Path对象
        )
    )
    # 构造当前网关服务实例元数据
    instance = ServiceInstance(
        service_name=settings.service_name,  # 服务名称
        ip=settings.service_advertise_host,  # 对外暴露的IP
        port=settings.service_port,  # 对外端口
        metadata={
            "version": "0.1.0",  # 服务版本号
            "protocol": "http",  # 通信协议
            "httpPort": str(settings.service_port),  # http端口，存为字符串
        },
    )
    await registry.register(instance)  # 把当前网关实例注册进Nacos
    # 初始化全局httpx客户端，设置各项超时
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.upstream_connect_timeout_seconds,  # 上游连接超时
            read=settings.upstream_read_timeout_seconds,  # 上游读取超时
            write=settings.upstream_write_timeout_seconds,  # 上游写入超时
            pool=settings.upstream_pool_timeout_seconds,  # 连接池获取连接超时
        )
    )
    # 初始化JWT编解码器
    codec = JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding)
    # 组装并返回完整依赖容器
    return GatewayContainer(
        settings=settings,
        redis=redis,
        registry=registry,
        registered_instance=instance,
        http_client=client,
        authenticator=GatewayAuthenticator(codec, redis),
        resolver=NacosUpstreamResolver(registry),
        proxy=ReverseProxy(client),
    )


# 创建网关应用
def create_app(
    container: GatewayContainer | None = None,
    *,  # 星号后面的参数只能按名字传参，不能按位置传
    settings: GatewaySettings | None = None,
) -> FastAPI:
    # 应用生命周期管理器，服务启动、关闭的时候跑对应的逻辑
    @asynccontextmanager  # 把下面函数包装成异步上下文管理器，给FastAPI的lifespan用
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None  # 判断：这个容器是不是咱们函数里面自己新建的
        # 优先用外面传进来的container，没传就根据配置新建一个容器
        active = container or await build_container(
            settings or GatewaySettings()  # type: ignore[call-arg]
        )
        app.state.container = active  # 把依赖容器挂到app上，后面接口中间件都能拿到
        try:
            yield  # 卡住在这里，服务正式跑起来，接收用户请求
        finally:  # 服务关闭的时候，这里一定会执行清理
            if owned:  # 只有咱们自己新建的容器，才去销毁资源
                await active.registry.deregister(active.registered_instance)  # Nacos把当前网关实例删掉
                await active.registry.close()  # 关掉Nacos客户端
                await active.http_client.aclose()  # 关掉httpx异步请求客户端
                await active.redis.aclose()  # 断开Redis连接

    # 拿配置：优先传入的settings，其次container里面的配置，都没有就是None
    configured_settings = settings or (container.settings if container is not None else None)
    # 解析跨域允许的域名列表，有配置就切分，没有就用模型默认值
    configured_origins = (
        configured_settings.cors_allowed_origins.split(",")
        if configured_settings is not None
        else GatewaySettings.model_fields["cors_allowed_origins"].default.split(",")
    )
    # 初始化FastAPI实例，填标题和版本，绑定生命周期函数
    app = FastAPI(title="InfiniteChat Gateway", version="0.1.0", lifespan=lifespan)
    # 添加跨域中间件
    app.add_middleware(
        CORSMiddleware,
        # 清理域名列表，去掉空字符串和两边空格
        allow_origins=[origin.strip() for origin in configured_origins if origin.strip()],
        # 是否允许带Cookie凭据，读配置，默认false
        allow_credentials=(
            configured_settings.cors_allow_credentials if configured_settings is not None else False
        ),
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],  # 允许哪些HTTP方法
        allow_headers=["*"],  # 请求头全部放行
        expose_headers=["X-Request-Id", "X-Correlation-Id"],  # 告诉前端可以拿到这两个响应头
        max_age=36_000,  # 浏览器预检请求缓存多久，单位秒
    )

    # 全局请求中间件，所有请求先进这里
    @app.middleware("http")  # 注册http类型的全局中间件
    async def request_context(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.monotonic()  # 记下请求开始时间，不受系统时间回拨影响
        outcome = "success"  # 请求结果标记，默认是成功
        try:
            # 绑定请求上下文，从请求头拿追踪ID，出作用域自动清理
            with bind_request_context(
                request_id=request.headers.get("X-Request-Id"),
                correlation_id=request.headers.get("X-Correlation-Id"),
            ) as context:
                response = await call_next(request)  # 继续往下走路由处理
                response.headers["X-Request-Id"] = context.request_id  # 把请求ID塞回响应头给前端
                response.headers["X-Correlation-Id"] = context.correlation_id  # 链路ID也塞回去
                if response.status_code >= 500:  # 5开头的服务错误，标记成失败
                    outcome = "error"
                return response
        except Exception:
            outcome = "error"  # 抛异常直接标记失败
            raise  # 异常继续往上抛，交给全局异常处理器
        finally:
            # Prometheus指标：请求计数+1
            GATEWAY_METRICS.requests.labels("GateWay", "http", request.url.path, outcome).inc()
            # Prometheus指标：记录这次请求花了多久
            GATEWAY_METRICS.request_duration.labels("GateWay", "http", request.url.path).observe(
                time.monotonic() - started
            )

    # k8s存活探针接口，判断进程有没有挂掉
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "UP"}

    # k8s就绪探针接口，判断能不能正常对外提供服务
    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, str]:
        active: GatewayContainer = request.app.state.container  # 取出全局依赖容器
        if not await active.redis.ping():  # ping一下Redis，不通就没就绪
            raise RuntimeError("Redis readiness failed")
        # 拿到所有需要路由的服务名，挨个检查有没有健康实例
        for service_name in {target.service_name for target in ROUTE_TARGETS}:
            if not await active.registry.discover(service_name, subscribe=False):
                raise RuntimeError(f"No healthy {service_name} instance")
        return {"status": "UP"}

    # Prometheus拉指标的接口
    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # 反向代理兜底路由，匹配剩下所有路径
    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def proxy_request(path: str, request: Request) -> Response:
        del path  # 不用这个捕获的path，直接从request拿完整url路径
        active: GatewayContainer = request.app.state.container  # 取出依赖容器
        target = target_for_path(request.url.path)  # 根据路径匹配上游服务配置
        if target is None:  # 没有匹配的路由，返回404
            body = BaseResponse[None].error(ErrorCode.NOT_FOUND_ERROR)
            return JSONResponse(status_code=404, content=body.to_wire())
        user_id: int | None = None  # 用户ID，默认没登录
        if not is_public_auth_exempt(request.url.path):  # 不在免登录白名单就要鉴权
            try:
                # 调用鉴权组件校验令牌，拿到用户id
                user_id = await active.authenticator.authenticate(
                    access_token=request.headers.get("Access-Token"),
                    refresh_token=request.headers.get("Refresh-Token"),
                )
            except GatewayAuthError as error:
                # 鉴权失败，返回401
                auth_body = GatewayAuthErrorResponse(code=error.code, message=error.message)
                return JSONResponse(status_code=401, content=auth_body.to_wire())
        context = current_request_context()  # 获取当前请求的上下文对象
        # 负载均衡哈希key：登录用户用user_id，没登录用request_id兜底
        routing_key = (
            str(user_id) if user_id is not None else context.request_id if context else "0"
        )
        try:
            # 服务发现，拿到上游服务地址
            upstream = await active.resolver.resolve(
                target.service_name,
                routing_key=routing_key,
            )
            # 转发请求到上游微服务，返回流式响应
            return await active.proxy.forward(
                request,
                upstream_base_url=upstream,
                authenticated_user_id=user_id,
            )
        except ClientDisconnect:  # 客户端自己关掉连接了，返回499
            return Response(status_code=499)
        except (httpx.HTTPError, LookupError):  # 上游服务连不上、找不到实例
            body = BaseResponse[None].error(ErrorCode.SERVICE_UNAVAILABLE)
            return JSONResponse(status_code=503, content=body.to_wire())

    return app

# 创建一个开箱即用的网关实例，不用外部传容器和配置
def create_default_app() -> FastAPI:
    return create_app()