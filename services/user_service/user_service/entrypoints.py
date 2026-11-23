"""Independent UserService API, Kafka worker and scheduler entrypoints."""
# 用户服务多入口文件：API服务、Kafka worker、调度器，分成独立进程启动

import asyncio  # 导入异步IO库

import uvicorn  # 导入ASGI服务框架，用来启动FastAPI

from user_service.config import UserServiceSettings
# 导入用户服务配置类

from user_service.integrations.kafka import UserKafkaProducer
# 导入封装好的Kafka生产者

from user_service.runtime import build_core_runtime
# 导入核心运行时构建函数，组装db、redis、id生成器等资源

from user_service.schedulers.friend_requests import FriendRequestExpirationScheduler
# 导入好友申请过期调度器类

from user_service.schedulers.runner import run_user_scheduler
# 导入调度器循环执行函数

from user_service.workers.friend_requests import FriendRequestHandlers
# 导入好友申请事件处理器

from user_service.workers.outbox_publisher import OutboxPublisher
# 导入发件箱发布器

from user_service.workers.runner import run_user_worker
# 导入worker主运行函数

# 读取配置，返回配置实例
def _settings() -> UserServiceSettings:
    return UserServiceSettings()  # type: ignore[call-arg]  # 从环境变量加载配置

# 启动API进程
def api_main() -> None:
    settings = _settings()  # 加载配置
    uvicorn.run(  # 启动uvicorn ASGI服务
        "user_service.app:create_default_app",  # app工厂函数路径
        factory=True,  # 开启工厂模式
        host=settings.service_host,  # 监听地址
        port=settings.service_port,  # 监听端口
        workers=1,  # 单进程运行
    )

# worker协程内部逻辑
async def _worker(settings: UserServiceSettings) -> None:
    core = build_core_runtime(settings)  # 初始化核心资源
    producer = UserKafkaProducer(  # 实例化Kafka生产者
        settings.kafka_bootstrap_servers,
        client_id=settings.kafka_client_id,
    )
    try:  # 捕获内部异常
        await run_user_worker(  # 启动worker，消费kafka+轮询outbox
            settings=settings,
            handlers=FriendRequestHandlers(  # 实例化好友申请处理器
                session_factory=core.session_factory,
                cache=core.cache,
            ),
            publisher=OutboxPublisher(  # 实例化发件箱发布器
                session_factory=core.session_factory,
                producer=producer,
                batch_size=settings.outbox_batch_size,
                max_attempts=settings.outbox_max_attempts,
            ),
            producer=producer,
        )
    finally:  # 无论成功失败都执行
        await core.close()  # 关闭db、redis连接

# 启动Worker进程
def worker_main() -> None:
    asyncio.run(_worker(_settings()))  # 开启事件循环，执行worker协程

# 调度器协程内部逻辑
async def _scheduler(settings: UserServiceSettings) -> None:
    core = build_core_runtime(settings)  # 初始化核心资源
    try:  # 捕获内部异常
        await run_user_scheduler(  # 启动调度器循环
            FriendRequestExpirationScheduler(  # 实例化好友过期调度器
                session_factory=core.session_factory,
                cache=core.cache,
                batch_size=settings.scheduler_batch_size,
                lease_ms=settings.scheduler_lease_ms,
            )
        )
    finally:  # 无论成功失败都执行
        await core.close()  # 关闭db、redis连接

# 启动调度进程
def scheduler_main() -> None:
    asyncio.run(_scheduler(_settings()))  # 开启事件循环，执行调度器协程