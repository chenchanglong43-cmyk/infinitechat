"""WebSocket 端点演示.

真实的实时服务应用, 只把 Kafka 生产者换成替身: 握手时在 Authorization 头里带 access 令牌;
ping 回 pong; 发出的消息由服务端补上消息 ID 和时间, 再交给 Kafka;
不带令牌、拿旧令牌都握不了手; 冒充别人发消息, 连接直接被关.
前置条件: 需 Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import json  # JSON 处理
import secrets  # 安全随机数
import warnings
from datetime import timedelta

import structlog  # 结构化日志
from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.contracts.websocket import MessageRequest
from infinitechat.foundation.ids import SnowflakeGenerator

from infinitechat.foundation.security import JwtCodec, TokenType

from infinitechat.foundation.telemetry import configure_logging

from realtime_service.app import RealtimeContainer, create_app
from realtime_service.auth import RealtimeAuthenticator
from realtime_service.config import RealtimeSettings

from realtime_service.connections import ConnectionManager
from redis.asyncio import Redis

from starlette.websockets import WebSocketDisconnect

from user_service.integrations.redis import UserRedis

with warnings.catch_warnings():
    # 新版 Starlette 的 TestClient 会提示改用 httpx2; 演示沿用工作区已有的 httpx, 这条提示不影响结果
    # 屏蔽测试客户端的提示警告, 输出更干净
    warnings.filterwarnings("ignore", message=".*starlette.testclient.*")
    from starlette.testclient import TestClient

logger = structlog.get_logger(__name__)

USER_ID = 9_000_009_401
OTHER_ID = 9_000_009_402

# 按 user 服务签发令牌的写法签一个, 带随机 jti
def issue(codec: JwtCodec, token_type: TokenType, ttl_seconds: int) -> str:
    """按 user 服务签发令牌的写法签一个, 带随机 jti."""
    return codec.encode(
        subject=str(USER_ID),
        expires_in=timedelta(seconds=ttl_seconds),
        token_type=token_type,
        additional_claims={"jti": secrets.token_hex(16)},
    )

# 模拟一次登录: 签一组令牌
async def login(codec: JwtCodec, store: UserRedis) -> str:
    """模拟一次登录: 签一组令牌, 按 user 服务的写法存成当前令牌, 返回 access."""
    access = issue(codec, TokenType.ACCESS, 1800)
    refresh = issue(codec, TokenType.REFRESH, 604_800)
    await store.store_tokens(
        USER_ID,
        access_token=access,
        refresh_token=refresh,
        access_ttl_seconds=1800,
        refresh_ttl_seconds=604_800,
    )
    return access

# 替身 Kafka 生产者: 不连 Kafka
class StubProducer:
    """替身 Kafka 生产者: 不连 Kafka, 把交来的入站消息记下来."""

    def __init__(self) -> None:  # 构造方法: 创建对象时把依赖存起来
        """还没有收到任何消息."""
        self.inbound: list[MessageRequest] = []  # 进来的(消息请求列表): 空列表

    # 替身不需要启动
    async def start(self) -> None:
        """替身不需要启动."""

    # 替身不需要停止
    async def stop(self) -> None:
        """替身不需要停止."""

    # 记下这条入站消息
    async def publish_inbound(self, message: MessageRequest) -> None:
        """记下这条入站消息."""
        self.inbound.append(message)  # 把 消息 追加到进来的

# 拼一条单聊文本消息帧
def frame(sender: int) -> str:
    """拼一条单聊文本消息帧, 发送人由参数决定."""
    return json.dumps(
        {
            "sessionId": 1,  # "sessionId": 1
            "receiverId": OTHER_ID,  # "receiverId": 其他ID
            "senderId": sender,  # "senderId": 发送人
            "type": 0,  # "type": 0
            "sessionType": 0,  # "sessionType": 0
            "body": {"content": "你好"},  # "body": 1 个键的字典
        }
    )

# 单独开一个 Redis 连接, 模拟登录
async def prepare(settings: RealtimeSettings) -> str:
    """单独开一个 Redis 连接, 模拟登录, 拿到当前的 access 令牌."""
    redis = Redis.from_url(
        settings.redis_url,
        db=settings.redis_db,
        decode_responses=True,
        protocol=2,
    )
    try:  # 下面这段可能出错, 出错交给 except 处理
        codec = JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding)
        return await login(codec, UserRedis(redis))
    finally:  # 不管成功还是出错, 最后都执行这里
        await redis.aclose()

def main() -> None:
    """装一个真实的实时服务应用, 走一遍握手和收发."""
    settings = RealtimeSettings(
        instance_id="realtime-demo",
        redis_url=read_env("REDIS_URL"),
        kafka_bootstrap_servers=read_env("KAFKA_BOOTSTRAP_SERVERS"),
        jwt_secret=read_env("JWT_SECRET"),
        service_jwt_secret=read_env("SERVICE_JWT_SECRET"),
        nacos_server_addr="127.0.0.1:8848",
        nacos_cache_dir=".runtime/nacos/cache",
        nacos_log_dir=".runtime/nacos/log",
        snowflake_worker_id=30,
        snowflake_data_center_id=30,
    )
    # 启动事件循环跑这个协程: 单独开一个 Redis 连接, 模拟登录
    access = asyncio.run(prepare(settings))
    # TestClient 在自己的事件循环里运行应用, 这个 Redis 客户端的连接随进程退出释放
    redis = Redis.from_url(
        settings.redis_url,
        db=settings.redis_db,
        decode_responses=True,
        protocol=2,
    )
    producer = StubProducer()  # 创建替身 Kafka 生产者: 不连 Kafka
    container = RealtimeContainer(
        settings=settings,
        redis=redis,
        manager=ConnectionManager(
            redis,
            instance_id=settings.instance_id,
            ttl_seconds=settings.connection_ttl_seconds,
            send_timeout_seconds=settings.send_timeout_seconds,
        ),
        auth=RealtimeAuthenticator(JwtCodec(settings.jwt_secret), redis),
        ids=SnowflakeGenerator(worker_id=30, data_center_id=30),
        producer=producer,
    )
    with TestClient(create_app(container)) as client:  # 创建测试客户端 client, 用完自动收尾
        # 1. 不带令牌握手: 直接被关
        try:  # 下面这段可能出错, 出错交给 except 处理
            # 建立 WebSocket 连接 socket, 用完自动收尾
            with client.websocket_connect("/ws/netty") as socket:
                socket.receive_text()  # 收一条文本消息
        except WebSocketDisconnect as error:
            logger.warning("不带令牌握手", close_code=error.code)  # 打一条 warning 日志
        # 2. 带当前令牌握手, ping 一下, 再发一条消息; 最后再 ping 一次, 保证消息已处理完
        auth_header = {"Authorization": access}  # 鉴权请求头: 1 个键的字典
        # 建立 WebSocket 连接 socket, 用完自动收尾
        with client.websocket_connect("/ws/netty", headers=auth_header) as socket:
            socket.send_text("ping")  # 发一条文本消息
            pong = socket.receive_text()
            socket.send_text(frame(USER_ID))  # 发一条文本消息
            socket.send_text("ping")  # 发一条文本消息
            socket.receive_text()  # 收一条文本消息
        sent = producer.inbound[-1]  # 已发: 生产者的进来的[-1]
        logger.info(  # 打一条 info 日志
            "收发",
            pong=pong,
            published=len(producer.inbound),
            message_id_assigned=sent.message_id is not None,
            created_time_set=sent.created_time is not None,
        )
        # 3. 签名有效, 但已经不是 Redis 里的当前令牌
        stale = issue(JwtCodec(settings.jwt_secret), TokenType.ACCESS, 1800)
        try:  # 下面这段可能出错, 出错交给 except 处理
            # 建立 WebSocket 连接 socket, 用完自动收尾
            with client.websocket_connect("/ws/netty", headers={"Authorization": stale}) as socket:
                socket.receive_text()  # 收一条文本消息
        except WebSocketDisconnect as error:
            logger.warning("旧令牌握手", close_code=error.code)  # 打一条 warning 日志
        # 4. 用自己的令牌握手, 却冒充别人发消息
        # 建立 WebSocket 连接 socket, 用完自动收尾
        with client.websocket_connect("/ws/netty", headers=auth_header) as socket:
            socket.send_text(frame(OTHER_ID))  # 发一条文本消息
            try:  # 下面这段可能出错, 出错交给 except 处理
                socket.receive_text()  # 收一条文本消息
            except WebSocketDisconnect as error:
                # 打一条 warning 日志
                logger.warning("冒充发送", close_code=error.code, published=len(producer.inbound))

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 装一个真实的实时服务应用, 走一遍握手和收发