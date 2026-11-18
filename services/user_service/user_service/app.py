"""UserService FastAPI application; workers and schedulers remain separate processes."""

import time  # 时间

from fastapi import FastAPI, Request

from infinitechat.foundation.context import bind_request_context

from infinitechat.foundation.telemetry import FoundationMetrics

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

USER_METRICS = FoundationMetrics.create(namespace="user_service")

# 创建应用
def create_app() -> FastAPI:
    # 初始化FastAPI服务，设置服务名称和版本
    app = FastAPI(title="InfiniteChat UserService", version="0.1.0")

    # 请求上下文
    @app.middleware("http")  # 注册HTTP中间件，每一次HTTP请求都会先走这个函数
    async def request_context(
        request: Request,  # request：本次请求对象，可以拿请求头、路径等信息
        call_next: RequestResponseEndpoint,  # call_next：把请求交给后面路由继续处理
    ) -> Response:  # 最终返回响应对象
        started = time.monotonic()  # 记录请求开始时间，用单调时钟，不会受系统校时影响
        outcome = "success"  # 默认标记请求结果为成功
        request_id = request.headers.get("X-Request-Id")  # 从请求头拿到请求ID
        try:  # 捕获后续路由抛出的异常
            with bind_request_context(  # 绑定请求上下文，with结束自动清理上下文
                request_id=request_id,
                correlation_id=request.headers.get("X-Correlation-Id"),
            ) as context:
                response = await call_next(request)  # 执行后面的路由逻辑，拿到返回响应
                # 在响应头回写请求ID，方便前端排查
                # 回写请求 ID 的好处:
                # - 前端报错弹窗里带上 X-Request-Id, 用户截图给你,
                #   你就能在日志里 grep 出这次请求的完整记录;
                # - 网关/APM 能拿这个 ID 做链路聚合;
                # - 排查线上问题时不用再问"大概几点、哪个用户", 一个 ID 就能定位。
                response.headers["X-Request-Id"] = context.request_id
                # 在响应头回写关联ID，链路追踪用
                response.headers["X-Correlation-Id"] = context.correlation_id
                if response.status_code >= 500:
                    outcome = "error"  # 修改结果标记为失败
                return response
        except Exception:  # 捕获任意异常
            outcome = "error"  # 标记结果为失败
            raise  # 异常继续向外抛出，交给全局异常处理器
        finally:  # 不管成功、报错，这段代码一定会执行，用来埋监控指标
            # 请求计数器 +1，打上标签：服务名、协议、接口路径、成功/失败结果
            USER_METRICS.requests.labels("UserService", "http", request.url.path, outcome).inc()
            # 记录本次请求耗时，单位秒，写入直方图指标
            USER_METRICS.request_duration.labels("UserService", "http", request.url.path).observe(
                time.monotonic() - started
            )

    # 存活探针
    @app.get("/health/live")  # 注册 GET 接口 /health/live
    # 存活探针
    async def live() -> dict[str, str]:
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