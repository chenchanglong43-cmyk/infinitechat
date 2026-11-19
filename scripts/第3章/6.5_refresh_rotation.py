"""刷新轮换演示.

refresh token 用一次就换新, 旧的再用被拒; 刷新以后旧的 access token 也失效;
登出以后两种令牌全部作废.
前置条件: 需本地 MySQL, Redis.
"""

import asyncio  # 异步编程
import uuid
from collections.abc import Awaitable

import structlog  # 结构化日志

from infinitechat.contracts.http import LoginSessionResponse, UserRegisterRequest
from infinitechat.foundation.security import JwtCodec

from infinitechat.foundation.telemetry import configure_logging

from user_service.auth import AuthService, PasswordManager
from user_service.config import UserServiceSettings
from user_service.errors import BusinessError

from user_service.runtime import CoreRuntime, build_core_runtime

logger = structlog.get_logger(__name__)

# 演示用的口令
TYPED_IN = "Passw0rd!"  # 常量 typed in: 演示用的口令

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

# 实时服务还没有, 先给一个固定地址
class FixedLocator:
    """实时服务还没有, 先给一个固定地址."""

    # 不管是谁, 都返回同一个地址
    async def locate(self, routing_key: str) -> str:
        """不管是谁, 都返回同一个地址."""
        return "127.0.0.1:9101/ws/netty"

# 按服务里的装配方式搭一个认证服务
def build_auth(
    settings: UserServiceSettings,
    core: CoreRuntime,
    sender: CapturingEmailSender,
) -> AuthService:
    """按服务里的装配方式搭一个认证服务, 只把邮件和定位换成替身."""
    return AuthService(
        session_factory=core.session_factory,
        cache=core.cache,
        email_sender=sender,
        realtime_locator=FixedLocator(),
        # password_manager: 创建密码管理器
        password_manager=PasswordManager(legacy_salt=settings.legacy_password_salt),
        # jwt_codec(JWT编解码器): 创建JWT 编解码
        jwt_codec=JwtCodec(settings.jwt_secret, key_encoding=settings.jwt_key_encoding),
        id_generator=core.ids,
        settings=settings,
    )

# 用一个新邮箱走完发验证码和注册
async def register(auth: AuthService, sender: CapturingEmailSender) -> LoginSessionResponse:
    """用一个新邮箱走完发验证码和注册, 注册成功就等于登录了一次."""
    # 邮箱: 字符串 "demo-{...的hex[切片]}@exa..."
    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    await auth.send_captcha(email)
    request = UserRegisterRequest(
        email=email,
        password=TYPED_IN,
        confirm_password=TYPED_IN,
        code=sender.codes[email],
    )
    return await auth.register(request)

async def expect_rejected(case: str, action: Awaitable[object]) -> None:
    """等一个应该失败的调用, 打出它被拒时的错误码."""
    try:  # 下面这段可能出错, 出错交给 except 处理
        await action
    except BusinessError as error:
        # 打一条 warning 日志
        logger.warning("被拒", case=case, error_code=int(error.code), message=error.message)
    else:  # 没有出异常才执行这里
        logger.error("没有被拒", case=case)  # 打一条 error 日志

# 注册拿到一组令牌, 刷新一次, 再登出
async def main() -> None:
    """注册拿到一组令牌, 刷新一次, 再登出, 每一步都拿旧令牌试一下."""
    settings = UserServiceSettings()
    core = build_core_runtime(settings)
    sender = CapturingEmailSender()
    auth = build_auth(settings, core, sender)
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 1. 注册即登录, 拿到第一组令牌, 马上刷新一次
        first = await register(auth, sender)
        rotated = await auth.refresh(first.refresh_token)
        logger.info(  # 打一条 info 日志
            "刷新一次",
            refresh_changed=rotated.refresh_token != first.refresh_token,
            access_changed=rotated.access_token != first.access_token,
        )
        # 2. 旧的 refresh 和旧的 access 都不认了, 新的 access 可用
        await expect_rejected("旧 refresh 再用", auth.refresh(first.refresh_token))
        await expect_rejected("旧 access", auth.authenticate_access_token(first.access_token))
        user_id = await auth.authenticate_access_token(rotated.access_token)
        logger.info("新 access 可用", same_user=user_id == first.user_id)  # 打一条 info 日志
        # 3. 登出以后, 手里剩下的两个令牌全部作废
        await auth.logout(first.user_id)
        await expect_rejected(
            "登出后的 access",
            auth.authenticate_access_token(rotated.access_token),
        )
        await expect_rejected("登出后的 refresh", auth.refresh(rotated.refresh_token))
    finally:  # 不管成功还是出错, 最后都执行这里
        await core.close()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 注册拿到一组令牌, 刷新一次