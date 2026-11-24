"""对外接口演示.

不起 Nacos: 依赖容器按 build_container 的写法手工装好, 只把注册表和邮件通道换成替身,
交给 create_app, 再用 httpx 直接调用应用. 注册拿到令牌, 带令牌查好友列表, 不带令牌被拒,
内部接口不带服务令牌返回 401; 注册响应里的实时地址由真实的定位器算出来.
httpx 是网关的依赖, 工作区所有成员共用一个虚拟环境, 装好网关以后脚本就能用它.
前置条件: 需本地 MySQL, Redis, 变量写在仓库根目录的 .env 里.
"""

import asyncio  # 异步编程
import uuid
from urllib.parse import urlsplit

import httpx  # HTTP 客户端
import structlog  # 结构化日志
from infinitechat.contracts import BaseResponse

from infinitechat.foundation.discovery import NacosRegistry, ServiceInstance

from infinitechat.foundation.security import JwtCodec, ServiceTokenCodec

from infinitechat.foundation.telemetry import configure_logging
from user_service.app import create_app

from user_service.auth import AuthService, PasswordManager
from user_service.config import UserServiceSettings
from user_service.contacts import ContactService
from user_service.dependencies import UserContainer
from user_service.groups import GroupService

from user_service.integrations.minio import AvatarStorage

from user_service.integrations.realtime_locator import RealtimeLocator

from user_service.internal_api import InternalValidationService

from user_service.runtime import CoreRuntime, build_core_runtime
from user_service.users import UserProfileService

logger = structlog.get_logger(__name__)

# 成功响应的业务码, 直接问契约要, 不在脚本里写死
# 常量 成功码: 成功响应的业务码, 直接问契约要, 不在脚本里写死
SUCCESS_CODE = BaseResponse[None].success(None).code

# 演示用的口令
TYPED_IN = "Passw0rd!"  # 常量 typed in: 演示用的口令

# 替身注册表: 不连 Nacos
class StubRegistry:
    """替身注册表: 不连 Nacos, 固定返回一个实时服务实例, 选实例用真实的算法."""

    choose_instance = staticmethod(NacosRegistry.choose_instance)

    # 不管问哪个服务
    async def discover(self, service_name: str) -> tuple[ServiceInstance, ...]:
        """不管问哪个服务, 都返回同一个带 WebSocket 元数据的实例."""
        metadata = {"wsPort": "9101", "wsPath": "/ws/netty"}  # metadata: 2 个键的字典
        return (
            ServiceInstance(service_name=service_name, ip="10.0.0.1", port=8102, metadata=metadata),
        )

# 假的邮件通道: 不真发邮件
class CapturingEmailSender:
    """假的邮件通道: 不真发邮件, 只把验证码记下来."""

    def __init__(self) -> None:  # 构造方法: 创建对象时把依赖存起来
        """还没收到任何验证码."""
        self.codes: dict[str, str] = {}  # codes(字典(字符串 -> 字符串)): 空字典

    # 把发给某个邮箱的验证码存起来
    async def send_captcha(self, target_email: str, code: str) -> None:
        """把发给某个邮箱的验证码存起来, 脚本自己去取."""
        self.codes[target_email] = code

def build_container(
    settings: UserServiceSettings,
    core: CoreRuntime,
    sender: CapturingEmailSender,
) -> UserContainer:
    """照 build_container 装依赖容器, 只把注册表和邮件通道换成替身."""
    registry = StubRegistry()  # 创建替身注册表: 不连 Nacos, 固定返回一个实时服务实例
    jwt_codec = JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding)
    hosts = {settings.minio_endpoint.rsplit(":", maxsplit=1)[0]}  # hosts: (调用 rsplit[0])
    hosts.add(urlsplit(settings.public_gateway_base_url).hostname or "")  # 加进去
    return UserContainer(
        settings=settings,
        engine=core.engine,
        session_factory=core.session_factory,
        cache=core.cache,
        # registry(注册中心): 注册中心
        registry=registry,  # type: ignore[arg-type]
        registered_instance=ServiceInstance(
            service_name=settings.service_name,
            ip="127.0.0.1",
            port=settings.service_port,
        ),
        auth=AuthService(
            session_factory=core.session_factory,
            cache=core.cache,
            email_sender=sender,
            # realtime_locator: 创建实时地址定位
            realtime_locator=RealtimeLocator(registry),  # type: ignore[arg-type]
            # password_manager: 创建密码管理器
            password_manager=PasswordManager(legacy_salt=settings.legacy_password_salt),
            jwt_codec=jwt_codec,
            id_generator=core.ids,
            settings=settings,
        ),
        contacts=ContactService(
            session_factory=core.session_factory,
            cache=core.cache,
            id_generator=core.ids,
        ),
        groups=GroupService(
            session_factory=core.session_factory,
            id_generator=core.ids,
            settings=settings,
        ),
        profiles=UserProfileService(
            session_factory=core.session_factory,
            avatar_storage=AvatarStorage(settings),
            allowed_avatar_hosts=frozenset(hosts),
        ),
        # internal(内部): 创建内部校验服务
        internal=InternalValidationService(session_factory=core.session_factory, cache=core.cache),
        service_tokens=ServiceTokenCodec(JwtCodec(settings.service_jwt_secret)),
    )

# 注册一个新账号, 然后分别带令牌
async def main() -> None:
    """注册一个新账号, 然后分别带令牌, 不带令牌, 不带服务令牌各调一次."""
    settings = UserServiceSettings()
    core = build_core_runtime(settings)
    sender = CapturingEmailSender()
    app = create_app(build_container(settings, core, sender))
    transport = httpx.ASGITransport(app=app)
    email = f"api-{uuid.uuid4().hex[:8]}@example.com"  # 邮箱: 字符串 "api-{...的hex[切片]}@exam..."
    try:  # 下面这段可能出错, 出错交给 except 处理
        async with (  # 进入应用的生命周期(启动并在结束时关闭)
            app.router.lifespan_context(app),  # 打开 进入应用的生命周期(启动并在结束时关闭)
            # 打开 建异步 HTTP 客户端
            httpx.AsyncClient(transport=transport, base_url="http://user-service") as client,
        ):
            # 1. 发验证码, 注册; 注册即登录, 响应里带着实时地址
            await client.get("/api/user/sendCaptcha", params={"targetEmail": email})
            body = {
                "email": email,  # "email": 邮箱
                "password": TYPED_IN,  # "password": typed in
                "confirmPassword": TYPED_IN,  # "confirmPassword": typed in
                "code": sender.codes[email],  # "code": 发送人的codes[邮箱]
            }
            registered = (await client.post("/api/user/register", json=body)).json()
            session = registered["data"]  # 会话: registered["data"]
            logger.info(  # 打一条 info 日志
                "注册",
                ok=registered["code"] == SUCCESS_CODE,
                netty_uri=session["nettyUri"],
            )
            # 2. 带令牌查自己的好友列表(刚注册, 还没有好友)
            # 路径: 字符串 "/api/contact/{会话["user..."
            path = f"/api/contact/{session['userId']}/friend"
            page = {"pageNum": 1, "pageSize": 20}  # 页: 2 个键的字典
            headers = {"Access-Token": session["accessToken"]}  # 请求头: 1 个键的字典
            friends = (await client.get(path, params=page, headers=headers)).json()
            logger.info(  # 打一条 info 日志
                "带令牌查好友列表",
                ok=friends["code"] == SUCCESS_CODE,
                total=friends["data"]["total"],
            )
            # 3. 不带令牌: HTTP 200, 业务码是未登录
            anonymous = await client.get(path, params=page)
            logger.warning(  # 打一条 warning 日志
                "不带令牌",
                status=anonymous.status_code,
                error_code=anonymous.json()["code"],
            )
            # 4. 内部接口不带服务令牌: HTTP 401
            params = {"userId": session["userId"]}
            internal = await client.get("/api/internal/user/status", params=params)
            logger.warning(  # 打一条 warning 日志
                "内部接口",
                status=internal.status_code,
                error_code=internal.json()["code"],
            )
    finally:  # 不管成功还是出错, 最后都执行这里
        await core.close()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 注册一个新账号