"""Independent WebSocket API and Kafka routing worker entrypoints."""
# 实时服务的两个独立进程入口：
#   ① API 进程：对外提供 WebSocket，管理连接
#   ② Worker 进程：消费 Kafka 推送事件，算收件人、投递消息
# 两者分开部署，各自扩缩容，互不影响

import asyncio  # 异步编程：启动事件循环、跑协程
from pathlib import Path  # 路径工具：Nacos 缓存/日志目录

import httpx  # HTTP 客户端：Worker 调 UserService 查群成员用
import uvicorn  # ASGI 服务器：跑 FastAPI 应用

from infinitechat.foundation.discovery import create_nacos_registry
# 创建 Nacos 注册中心客户端

from infinitechat.foundation.security import JwtCodec, ServiceTokenCodec
# JWT 编解码；服务令牌编解码（Worker 调别的服务时证明身份）

from infinitechat.foundation.settings import NacosSettings
# Nacos 配置对象

from redis.asyncio import Redis
# 异步 Redis 客户端

from realtime_service.app import build_container, create_app
# 构建依赖容器、创建 FastAPI 应用

from realtime_service.clients import RealtimeClients
# 调 UserService 的客户端封装（查会话成员）

from realtime_service.config import RealtimeSettings
# 实时服务配置

from realtime_service.delivery import RedisDeliveryBus
# Redis 投递总线：把消息发到用户所在实例的频道

from realtime_service.kafka import RealtimeProducer
# Kafka 生产者：Worker 里暂时也建了一个（当前 router 用不到 producer）

from realtime_service.routing import MessageRouter
# 消息路由：私聊发两人、群聊查成员后逐个投递

from realtime_service.worker import run_worker
# Worker 主循环：同时消费 PUSH 和 SYSTEM_NOTIFICATION

# 加载配置
def settings() -> RealtimeSettings:
    # 从环境变量/配置文件读取实时服务配置
    return RealtimeSettings()  # type: ignore[call-arg]

# 启动 WebSocket API
async def run_api(configured: RealtimeSettings) -> None:
    # ① 构建容器：连 Redis、注册 Nacos、组装连接管理器/鉴权/雪花/Kafka 生产者
    container = await build_container(configured)
    # ② 创建 uvicorn 服务器，托管 FastAPI 应用
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(container),  # FastAPI 应用（含 /ws/netty、探针、指标）
            host=configured.service_host,  # 监听地址
            port=configured.websocket_port,  # 监听端口
            workers=1,  # 单进程；多副本靠多部署几个容器
            ws="websockets-sansio",  # WebSocket 实现
            ws_max_size=configured.max_frame_bytes,  # 单帧最大字节数，超了拒绝
        )
    )
    try:  # 下面这段可能出错, 出错交给 except 处理
        await server.serve()  # 阻塞在这里，直到服务被关闭
    finally:  # 不管成功还是出错, 最后都执行这里
        # 优雅退出：从 Nacos 摘掉自己，关注册中心，关 Redis
        if container.registry is not None and container.instance is not None:
            await container.registry.deregister(container.instance)
            await container.registry.close()
        await container.redis.aclose()

# API 进程入口
def api_main() -> None:
    # 同步入口：启动事件循环，跑 run_api
    asyncio.run(run_api(settings()))

# 启动路由 Worker
async def run_routing_worker(configured: RealtimeSettings) -> None:
    # ① Worker 也要连 Redis：查连接归属、pub/sub 投递
    redis = Redis.from_url(
        configured.redis_url,
        db=configured.redis_db,
        decode_responses=True,
        protocol=2,
    )
    # ② Worker 也要 Nacos：调用 UserService 前要先发现实例
    registry = await create_nacos_registry(
        NacosSettings(
            nacos_server_addr=configured.nacos_server_addr,
            nacos_namespace=configured.nacos_namespace,
            nacos_username=configured.nacos_username,
            nacos_password=configured.nacos_password,
            nacos_cache_dir=Path(configured.nacos_cache_dir),
            nacos_log_dir=Path(configured.nacos_log_dir),
        )
    )
    # ③ HTTP 客户端：调用 UserService 查群成员（连接超时 3 秒，总超时 30 秒）
    http = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3))
    # ④ 用户服务客户端：带服务令牌，证明是 RealTimeService 在调用
    clients = RealtimeClients(
        registry,
        http,
        ServiceTokenCodec(JwtCodec(configured.service_jwt_secret)),
    )
    # ⑤ Kafka 生产者：事务ID加 -worker 后缀，避免和 API 进程的事务ID冲突
    producer = RealtimeProducer(
        configured.kafka_bootstrap_servers,
        instance_id=f"{configured.instance_id}-worker",
    )
    # ⑥ 路由器：投递总线 + 用户服务客户端 + 生产者 + Redis
    router = MessageRouter(
        delivery=RedisDeliveryBus(redis),
        users=clients,
        producer=producer,
        redis=redis,
    )
    # 启动生产者（虽然当前 router 主要用 delivery，不直接发 Kafka）
    await producer.start()
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 进入 Worker 主循环：消费 PUSH 和 SYSTEM_NOTIFICATION，直到进程退出
        await run_worker(configured, router)
    finally:  # 不管成功还是出错, 最后都执行这里
        # 清理顺序：停生产者 → 关 HTTP → 关 Nacos → 关 Redis
        await producer.stop()
        await http.aclose()
        await registry.close()
        await redis.aclose()

# Worker 进程入口
def worker_main() -> None:
    # 同步入口：启动事件循环，跑路由 Worker
    asyncio.run(run_routing_worker(settings()))
