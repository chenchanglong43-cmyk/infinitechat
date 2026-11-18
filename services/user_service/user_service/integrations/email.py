"""Async SMTP captcha delivery."""

# 这里邮件正文有中文标点，关掉ruff对应的检查
# ruff: noqa: RUF001

from email.message import EmailMessage

import aiosmtplib

from user_service.config import UserServiceSettings


# SMTP邮件发送工具类，专门用来发验证码邮件
class SmtpEmailSender:
    # 构造函数，实例化的时候把配置传进来存好
    def __init__(self, settings: UserServiceSettings) -> None:
        self._settings = settings

    # 异步方法：给目标邮箱发送验证码
    async def send_captcha(self, target_email: str, code: str) -> None:
        # 新建一封邮件对象
        message = EmailMessage()
        # 发件人邮箱，从配置里拿
        message["From"] = self._settings.smtp_sender
        # 收件人邮箱，调用方传进来的
        message["To"] = target_email
        # 邮件标题
        message["Subject"] = "【验证码】"
        # 设置邮件正文，把验证码填进去，告诉用户5分钟过期
        message.set_content(f"您的验证码是：{code}，5分钟内有效。")

        # 调用aiosmtplib异步发送邮件，不用阻塞事件循环
        await aiosmtplib.send(
            message,
            # smtp服务器地址
            hostname=self._settings.smtp_host,
            # smtp端口号
            port=self._settings.smtp_port,
            # smtp登录账号
            username=self._settings.smtp_username,
            # smtp密码，是SecretStr类型，调用get_secret_value拿到明文
            password=self._settings.smtp_password.get_secret_value(),
            # 是否开启tls加密，配置里控制
            use_tls=self._settings.smtp_use_tls,
            # 超时时间10秒，防止一直卡着
            timeout=10,
        )