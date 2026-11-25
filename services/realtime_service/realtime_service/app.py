"""Realtime WebSocket service: container, heartbeat and app factory."""
# 实时 WebSocket 服务：依赖容器、心跳刷新、应用工厂（文件后半部分还有 WS 接口和 lifespan）

import asyncio  # 异步编程：后台心跳任务、sleep
import time  # 时间：埋点计时、生成时间戳
from collections.abc import AsyncIterator  # 异步迭代器类型：lifespan 的返回类型
from contextlib import asynccontextmanager  # 把函数包装成异步上下文管理器，给 FastAPI lifespan 用
from dataclasses import dataclass  # 数据类：自动生成构造函数，用来装依赖
from pathlib import Path  # 路径工具：Nacos 缓存目录、日志目录
from typing import cast  # 类型断言：告诉类型检查器“这个值就是某个类型”
from uuid import uuid4  # 生成唯一 ID：连接 ID、实例 ID 等

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
# FastAPI 应用、HTTP 请求、WebSocket 对象、WebSocket 断开异常

from infinitechat.contracts.websocket import PING, PONG, MessageRequest
# WebSocket 协议常量（心跳 ping/pong）和聊天消息模型

from infinitechat.foundation.context import bind_request_context
# 请求上下文：绑定 request_id / correlation_id，日志里串链路用

from infinitechat.foundation.discovery import NacosRegistry, ServiceInstance, create_nacos_registry
# Nacos 注册中心：注册当前实例、发现其他服务

from infinitechat.foundation.ids import LeaseStore, SnowflakeGenerator, SnowflakeNodeLease
# 雪花 ID：生成器 + 租约（保证多实例 worker_id 不冲突）

from infinitechat.foundation.security import JwtCodec
# JWT 编解码：握手时校验用户的 access token

from infinitechat.foundation.settings import NacosSettings
# Nacos 配置对象：地址、命名空间、账号密码

from infinitechat.foundation.telemetry import FoundationMetrics
# 监控指标：请求计数、耗时

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
# Prometheus：把指标导出成文本，供 /metrics 使用

from redis.asyncio import Redis
# 异步 Redis 客户端：连接归属、pub/sub 投递

from starlette.middleware.base import RequestResponseEndpoint
# 中间件回调类型

from starlette.responses import Response
# 基础响应类：/metrics 等接口返回用

from realtime_service.auth import RealtimeAuthenticator
# 实时服务鉴权器：握手时校验 token

from realtime_service.config import RealtimeSettings
# 实时服务配置

from realtime_service.connections import Connection, ConnectionManager
# 连接实体、本机连接管理器

from realtime_service.delivery import run_local_delivery
# 本机订阅协程：收到 Redis 投递包后推给 WebSocket

from realtime_service.kafka import RealtimeProducer
# Kafka 生产者：把消息写进 STORE / PUSH

REALTIME_METRICS = FoundationMetrics.create(namespace="realtime_service")
# 全局指标实例：命名空间 realtime_service

# 实时服务依赖容器
@dataclass(slots=True)  # 数据类: 自动生成构造函数等方法, 省内存
# 实时服务依赖容器
class RealtimeContainer:
    settings: RealtimeSettings  # 配置：realtime配置
    redis: Redis  # Redis 客户端：连接归属、pub/sub
    manager: ConnectionManager  # 本机 WebSocket 连接管理器
    auth: RealtimeAuthenticator  # 握手鉴权器
    ids: SnowflakeGenerator  # 雪花 ID 生成器
    producer: RealtimeProducer  # Kafka 生产者
    registry: NacosRegistry | None = None  # Nacos 注册中心（没有就为空）
    instance: ServiceInstance | None = None  # 当前实例注册信息（没有就为空）

# 刷新连接心跳
async def heartbeat(manager: ConnectionManager, interval: int) -> None:
    # 后台常驻：每隔 interval 秒给本机所有连接续一次 Redis TTL
    while True:  # 一直循环, 直到 break, return 或出错
        await manager.refresh()  # 刷新连接归属 TTL + 实例心跳 key
        await asyncio.sleep(interval)  # 睡 interval 秒，避免空转

# 创建 FastAPI 应用
def create_app(container: RealtimeContainer) -> FastAPI:
    # 雪花租约：保证这台实时服务实例独占一个 worker_id
    # cast(LeaseStore, container.redis)：Redis 客户端本身就有 set/eval，满足租约存储协议
    lease = SnowflakeNodeLease(
        cast(LeaseStore, container.redis),
        data_center_id=container.settings.snowflake_data_center_id,
        worker_id=container.settings.snowflake_worker_id,
        owner_label=f"{container.settings.service_name}:api",
    )
    # 关闭开关：服务关闭时 set，用来叫停后台续租任务
    lease_stop = asyncio.Event()

    @asynccontextmanager  # 把下面的函数变成异步上下文管理器
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # ① 抢 worker_id：抢不到会抛异常，服务直接启动失败
        await lease.acquire()
        # ② 后台续租：每 TTL/3 秒续一次，防止进程卡住后编号被别人抢走
        lease_task = asyncio.create_task(lease.maintain(lease_stop))
        # ③ 标记“必须持有租约”，就绪探针会检查这个标记
        _app.state.snowflake_lease_required = True
        # ④ 启动 Kafka 生产者，后面入站消息要用
        await container.producer.start()
        # ⑤ 后台心跳：定时给本机所有连接续 Redis TTL
        heartbeat_task = asyncio.create_task(
            heartbeat(container.manager, container.settings.heartbeat_seconds)
        )
        # ⑥ 后台订阅本机投递频道：收到 Redis 投递包就推给 WebSocket
        delivery_task = asyncio.create_task(run_local_delivery(container.redis, container.manager))
        try:  # 下面这段可能出错, 出错交给 except 处理
            yield  # 交出：服务正式对外提供 WS 连接
        finally:  # 不管成功还是出错, 最后都执行这里
            # 关闭顺序：先停租约，再停后台任务，最后停 Kafka 生产者
            lease_stop.set()  # 叫停 maintain
            await lease_task  # 等续租任务真正退出
            await lease.release()  # 删掉 Redis 里的租约，让编号可复用
            heartbeat_task.cancel()  # 取消心跳任务
            delivery_task.cancel()  # 取消投递任务
            # gather 等两个任务结束；return_exceptions 吞掉 CancelledError，避免关闭时报错
            await asyncio.gather(
                heartbeat_task,
                delivery_task,
                return_exceptions=True,
            )
            await container.producer.stop()  # 关闭 Kafka 生产者

    app = FastAPI(title="InfiniteChat RealTimeService", version="0.1.0", lifespan=lifespan)
    app.state.snowflake_lease = lease
    # 启动完成前先不要求租约；lifespan 抢到以后会改成 True
    app.state.snowflake_lease_required = False

    @app.middleware("http")  # 注册中间件: 每个 HTTP 请求都会先经过这里
    async def request_metrics(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.monotonic()  # 记开始时间（单调时钟，不怕系统时间被改）
        outcome = "success"  # 默认成功，后面按状态码/异常改成 error
        try:  # 下面这段可能出错, 出错交给 except 处理
            with bind_request_context(  # 绑定请求上下文 context, 用完自动收尾
                request_id=request.headers.get("X-Request-Id"),  # 沿用上游传来的请求ID
                correlation_id=request.headers.get("X-Correlation-Id"),  # 链路ID
            ) as context:
                response = await call_next(request)  # 继续走后面的路由
                # 把请求ID、链路ID写回响应头，方便前端/上游排查
                response.headers["X-Request-Id"] = context.request_id
                response.headers["X-Correlation-Id"] = context.correlation_id
                if response.status_code >= 500:  # 5xx 算失败
                    outcome = "error"
                return response
        except Exception:  # 任何异常都在这里接住
            outcome = "error"
            raise  # 把刚才的异常原样抛出去，交给上层处理
        finally:  # 不管成功还是出错, 最后都执行这里
            # 指标①：请求计数 +1（服务名、协议、路径、结果）
            REALTIME_METRICS.requests.labels(
                "RealTimeService",
                "http",
                request.url.path,
                outcome,
            ).inc()
            # 指标②：记录这次请求耗时（秒）
            REALTIME_METRICS.request_duration.labels(
                "RealTimeService",
                "http",
                request.url.path,
            ).observe(time.monotonic() - started)

    # WebSocket 鉴权握手与收发循环
    @app.websocket("/ws/netty")  # 注册 WebSocket 端点 /ws/netty
    # WebSocket 鉴权握手与收发循环
    async def websocket_endpoint(websocket: WebSocket) -> None:
        # 从握手请求头里拿 Authorization（一般是 Bearer <access_token>）
        authorization = websocket.headers.get("Authorization")
        # 校验令牌，拿到用户ID；失败返回 None
        user_id = await container.auth.authenticate(authorization)
        if user_id is None:
            # 鉴权失败：指标记一条 error
            REALTIME_METRICS.requests.labels(
                "RealTimeService",
                "websocket",
                "handshake",
                "error",
            ).inc()
            # 1008 = policy violation，拒绝连接
            await websocket.close(code=1008)
            return
        # 鉴权通过，正式建立 WebSocket 连接
        await websocket.accept()
        # 握手成功指标 +1
        REALTIME_METRICS.requests.labels(
            "RealTimeService",
            "websocket",
            "handshake",
            "success",
        ).inc()
        # 每条连接一个随机 connection_id，后面 Redis 归属和 Lua 校验都用它
        connection = Connection(user_id, uuid4().hex, websocket)
        # 登记到本机连接表 + 写 Redis 归属；返回这个用户之前的旧连接（如果有）
        previous = await container.manager.add(connection)
        if previous is not None:
            # 同一用户重复登录：把旧连接正常关闭（1000 = normal closure）
            await previous.websocket.close(code=1000)
        try:  # 下面这段可能出错, 出错交给 except 处理
            while True:  # 一直循环，直到客户端断开或出错
                payload = await websocket.receive_text()  # 等客户端发一条文本
                # 单帧太大直接拒绝，防止有人塞超大报文
                if len(payload.encode()) > container.settings.max_frame_bytes:
                    await websocket.close(code=1009)  # 1009 = message too big
                    return
                # 心跳 ping：回一个 pong，不当作聊天消息处理
                if payload == PING:
                    await websocket.send_text(PONG)
                    REALTIME_METRICS.requests.labels(
                        "RealTimeService",
                        "websocket",
                        "ping",
                        "success",
                    ).inc()
                    continue  # 跳过这一轮，继续等下一条
                # 解析成聊天消息；格式不合法会抛校验异常，连接随之中断
                message = MessageRequest.model_validate_json(payload)
                # 防止伪造：客户端传来的 sender_id 必须和登录用户一致
                if message.sender_id != user_id:
                    await websocket.close()
                    return
                # 再强制覆盖一次，确保后面用的就是鉴权后的用户ID
                message.sender_id = user_id
                # 服务端生成全局唯一消息ID（雪花算法）
                message.message_id = container.ids.next_id()
                # 消息创建时间：当前毫秒时间戳
                message.created_time = time.time_ns() // 1_000_000
                # 写 Kafka：同一个事务里发 STORE（存档）和 PUSH（推送）
                await container.producer.publish_inbound(message)
                # 消息处理成功指标 +1
                REALTIME_METRICS.requests.labels(
                    "RealTimeService",
                    "websocket",
                    "message",
                    "success",
                ).inc()
        except WebSocketDisconnect:
            # 客户端主动断开：正常情况，不用处理
            pass
        finally:  # 不管正常断开还是出错，最后都要清理
            # 删本机连接、Lua 删 Redis 归属、写离线时间
            await container.manager.remove(connection)

    # 存活探针
    @app.get("/health/live")  # 注册 GET 接口 /health/live
    # 存活探针
    async def live() -> dict[str, str]:
        # 能响应就说明进程还活着，不检查依赖
        return {"status": "UP"}

    # 就绪探针
    @app.get("/health/ready")  # 注册 GET 接口 /health/ready
    # 就绪探针
    async def ready() -> dict[str, str]:
        # 如果服务要求必须持有雪花租约，但现在 held=False（续租失败被抢走了）
        # 就返回错误，让 K8s 把这个实例摘掉，别再生成可能重复的 ID
        if app.state.snowflake_lease_required and not lease.held:
            raise RuntimeError("Snowflake node lease lost")
        # Redis 不通也别接流量：连接归属、投递都依赖 Redis
        if not await container.redis.ping():
            raise RuntimeError("Redis readiness failed")
        return {"status": "UP"}

    # Prometheus 指标
    @app.get("/metrics")  # 注册 GET 接口 /metrics
    # Prometheus 指标
    async def metrics() -> Response:
        # 把当前进程的指标导出成 Prometheus 文本格式
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app

# 构建服务容器
async def build_container(settings: RealtimeSettings) -> RealtimeContainer:
    # ① 连 Redis：连接归属、pub/sub 投递、握手鉴权都要用
    redis = Redis.from_url(
        settings.redis_url,
        db=settings.redis_db,  # 用配置指定的库号
        decode_responses=True,  # 读出来直接是 str，不用自己 decode
        protocol=2,  # RESP2，兼容性更好
    )
    # ② 建 Nacos 客户端：后面注册自己、发现用户服务都要用
    registry = await create_nacos_registry(
        NacosSettings(
            nacos_server_addr=settings.nacos_server_addr,  # Nacos 地址
            nacos_namespace=settings.nacos_namespace,  # 命名空间
            nacos_username=settings.nacos_username,  # 账号
            nacos_password=settings.nacos_password,  # 密码
            nacos_cache_dir=Path(settings.nacos_cache_dir),  # 本地缓存目录
            nacos_log_dir=Path(settings.nacos_log_dir),  # 日志目录
        )
    )
    # ③ 描述“我自己”这个实时服务实例，注册到 Nacos
    instance = ServiceInstance(
        service_name=settings.service_name,  # 服务名，比如 RealTimeService
        ip=settings.service_advertise_host,  # 对外暴露的 IP（别的服务能连到的地址）
        port=settings.websocket_port,  # 本机 WebSocket 端口
        metadata={
            # 元数据：告诉服务发现的使用方“我是 HTTP + WebSocket”
            "protocol": "http+websocket",
            "httpPort": str(settings.websocket_port),  # HTTP 端口（探针、指标也走这个）
            "wsPort": str(settings.websocket_port),  # WebSocket 端口
            "wsPath": settings.websocket_path,  # WebSocket 路径，比如 /ws/netty
            # WebSocket 公网地址（去掉末尾斜杠），前端/网关可以直接拼这个地址
            "wsPublicUrl": (settings.websocket_public_url or "").rstrip("/"),
            "instanceId": settings.instance_id,  # 实例ID：Redis key、Kafka 事务都用它区分
            "version": "0.1.0",  # 版本号
        },
    )
    # 真正注册到 Nacos：之后别的服务 discover 就能看到这台实例
    await registry.register(instance)
    # ④ 组装依赖容器，交给 create_app 使用
    return RealtimeContainer(
        settings=settings,  # 配置
        redis=redis,  # Redis 客户端
        manager=ConnectionManager(
            redis,  # 连接管理器内部也要用 Redis 写归属
            instance_id=settings.instance_id,  # 当前实例ID
            ttl_seconds=settings.connection_ttl_seconds,  # 连接归属 TTL（心跳续期）
            send_timeout_seconds=settings.send_timeout_seconds,  # 单条 WS 发送超时
        ),
        auth=RealtimeAuthenticator(
            JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding),  # 用户 JWT 编解码
            redis,  # 还要和 Redis 里当前 token 比对（支持踢下线）
        ),
        ids=SnowflakeGenerator(
            worker_id=settings.snowflake_worker_id,  # 工作节点编号
            data_center_id=settings.snowflake_data_center_id,  # 数据中心编号
        ),
        producer=RealtimeProducer(
            settings.kafka_bootstrap_servers,  # Kafka 地址
            instance_id=settings.instance_id,  # 事务ID要用，保证多实例互不干扰
        ),
        registry=registry,  # 注册中心
        instance=instance,  # 自己的注册信息，关闭时注销用
    )