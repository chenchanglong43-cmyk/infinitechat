"""邮件验证码发送演示.

生成 6 位数字验证码, 通过 SMTP 发到指定邮箱.
前置条件: 根目录 .env 里的 SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / SMTP_SENDER 已填好.
"""

import asyncio  # 异步编程
import secrets  # 生成密码学安全的随机数

import structlog  # 结构化日志

from infinitechat.foundation.telemetry import configure_logging

from user_service.config import UserServiceSettings

from user_service.integrations.email import SmtpEmailSender

logger = structlog.get_logger(__name__)

# 收件邮箱：演示用的固定地址
TARGET_EMAIL = "13609564325@163.com"


# 生成验证码并发送
async def main() -> None:
    """生成 6 位验证码, 调用 SmtpEmailSender 发送到目标邮箱."""
    # 读根目录 .env 里的配置(SMTP 账号、密码、发件人都在里面)
    settings = UserServiceSettings()  # type: ignore[call-arg]
    # 生成 6 位数字验证码, 不足 6 位前面补 0
    code = f"{secrets.randbelow(1_000_000):06d}"
    # 实例化发信工具类
    sender = SmtpEmailSender(settings)
    # 异步发信, 内部走 aiosmtplib, 不阻塞事件循环
    await sender.send_captcha(TARGET_EMAIL, code)
    # 演示脚本才把验证码打到日志里, 生产环境不要记录验证码
    logger.info("验证码已发送", target=TARGET_EMAIL, code=code)


if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 生成验证码并发送
