"""单点登录演示.

同一个账号再登录一次, 上一次拿到的 access token 立刻失效; 密码错和账号不存在报同一个错.
前置条件: 需本地 MySQL, Redis.
"""

import asyncio  # 异步框架
import uuid

import structlog  # 结构化日志，方便排查问题
from infinitechat.contracts.http import (
    LoginSessionResponse,
    PasswordLoginRequest,
    UserRegisterRequest,
)
from infinitechat.foundation.security import JwtCodec

from infinitechat.foundation.telemetry import configure_logging

from user_service.auth import AuthService, PasswordManager
from user_service.config import UserServiceSettings
from user_service.errors import BusinessError

from user_service.runtime import CoreRuntime, build_core_runtime

logger = structlog.get_logger(__name__)

# 演示用的正确密码、错误密码
TYPED_IN = "Passw0rd!"
WRONG_INPUT = "Wrong-0000"

# 模拟邮件发送，不真实发邮件，把验证码存内存里
class CapturingEmailSender:
    """假的邮件通道: 不真发邮件, 只把验证码记下来."""

    def __init__(self) -> None:
        """用来存邮箱-验证码的映射"""
        self.codes: dict[str, str] = {}

    async def send_captcha(self, target_email: str, code: str) -> None:
        """收到验证码，直接存到字典，脚本后面自己读取"""
        self.codes[target_email] = code

# 模拟实时节点服务，固定返回一个ws地址
class FixedLocator:
    """实时服务还没有, 先给一个固定地址."""

    async def locate(self, routing_key: str) -> str:
        """不管哪个用户，都返回同一个实时连接地址"""
        return "127.0.0.1:9101/ws/netty"

# 组装认证服务，把上面的替身依赖注入进去
def build_auth(
    settings: UserServiceSettings,
    core: CoreRuntime,
    sender: CapturingEmailSender,
) -> AuthService:
    """按正式服务的方式组装AuthService，邮件、实时定位换成模拟对象"""
    return AuthService(
        session_factory=core.session_factory,
        cache=core.cache,
        email_sender=sender,
        realtime_locator=FixedLocator(),
        password_manager=PasswordManager(legacy_salt=settings.legacy_password_salt),
        jwt_codec=JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding),
        id_generator=core.ids,
        settings=settings,
    )

# 注册流程：生成随机邮箱、发验证码、拿验证码注册
async def register(auth: AuthService, sender: CapturingEmailSender) -> LoginSessionResponse:
    """新邮箱注册，注册成功直接返回登录会话"""
    # 随机生成邮箱，避免重复
    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    # 调用接口发送验证码（只是存内存）
    await auth.send_captcha(email)
    request = UserRegisterRequest(
        email=email,
        password=TYPED_IN,
        confirm_password=TYPED_IN,
        code=sender.codes[email],
    )
    return await auth.register(request)

async def main() -> None:
    """核心演示流程：注册→二次登录→旧token失效→密码/账号不存在统一报错"""
    settings = UserServiceSettings()
    core = build_core_runtime(settings)
    sender = CapturingEmailSender()
    auth = build_auth(settings, core, sender)
    try:
        # 1. 注册账号，拿到第一套令牌
        first = await register(auth, sender)
        user_id = await auth.authenticate_access_token(first.access_token)
        logger.info("注册即登录", same_user=user_id == first.user_id, netty_uri=first.netty_uri)

        # 2. 同一账号再次登录，生成新令牌
        second = await auth.login_password(
            PasswordLoginRequest(email=first.email, password=TYPED_IN)
        )
        user_id = await auth.authenticate_access_token(second.access_token)
        logger.info("再登录一次", same_user=user_id == first.user_id)

        # 3. 校验旧token，预期直接报错，实现单点踢下线
        try:
            await auth.authenticate_access_token(first.access_token)
        except BusinessError as error:
            logger.warning("旧令牌被拒", error_code=int(error.code), message=error.message)

        # 4. 测试：密码错误、账号不存在，返回相同错误码（防账号探测）
        nobody = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
        for case, request in (
            ("密码错", PasswordLoginRequest(email=first.email, password=WRONG_INPUT)),
            ("账号不存在", PasswordLoginRequest(email=nobody, password=TYPED_IN)),
        ):
            try:
                await auth.login_password(request)
            except BusinessError as error:
                logger.warning(
                    "登录失败",
                    case=case,
                    error_code=int(error.code),
                    message=error.message,
                )
    finally:
        # 收尾，关闭数据库、redis连接
        await core.close()

if __name__ == "__main__":
    # 配置日志，控制台友好输出
    configure_logging(json_output=False)
    # 启动异步入口，跑演示脚本
    asyncio.run(main())