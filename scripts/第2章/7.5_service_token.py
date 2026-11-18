"""服务令牌演示.

只认白名单里的调用方; 被篡改, 拿用户令牌冒充, 过期的令牌一律被拒.
前置条件: 纯函数, 无需密钥/中间件.
"""

import time
# time：用来睡眠，制造过期场景
from datetime import timedelta
# timedelta：时间差，用来设置token有效期

import structlog
# structlog：结构化日志库，方便统一格式打印日志
from infinitechat.foundation.security import (
    JwtCodec,
    ServiceIdentity,
    ServiceTokenCodec,
    TokenExpiredError,
    TokenInvalidError,
    TokenType,
)

from infinitechat.foundation.telemetry import configure_logging
# 日志初始化函数，项目统一的日志配置入口

logger = structlog.get_logger(__name__)
# 获取当前模块的日志实例，后面打日志都用它

# 演示用签名密钥，HS256要求至少32字符，生产环境不要硬编码写在这里
DEMO_SIGNING_KEY = "demo-service-secret-0123456789abcdef"
# 允许调用的服务白名单，frozenset不可变，防止代码里意外修改
ALLOWED_CALLERS = frozenset({"RealTimeService", "OfflineDataService"})

def check(case: str, codec: ServiceTokenCodec, token: str) -> None:
    """拿一个令牌去验，打出放行的调用方，或者被拒的原因."""
    try:
        # 执行校验：验签名、过期、token类型、白名单
        identity = codec.verify(token, allowed_callers=ALLOWED_CALLERS)
    except (TokenInvalidError, TokenExpiredError) as error:
        # 校验失败，打印警告日志，记录是哪个测试场景、异常类型
        logger.warning("被拒", case=case, error=type(error).__name__)
        return
    # 全部校验通过，打印放行日志，输出调用方名字
    logger.info("放行", case=case, caller=identity.service_name)

def tamper(token: str) -> str:
    """把签名正中间的一个字符换掉，其余原样保留."""
    # JWT格式是 头.载荷.签名，按点切成三段
    head, body, signature = token.split(".")
    # 找到签名部分中间位置
    middle = len(signature) // 2
    # 翻转字符，用来制造篡改
    replacement = "A" if signature[middle] != "A" else "B"
    # 拼接回新token，签名已经被改动，校验会失败
    return ".".join((head, body, signature[:middle] + replacement + signature[middle + 1 :]))

def main() -> None:
    """白名单内外各验一次，再试篡改、冒充和过期三种坏令牌."""
    # 初始化服务token工具，用演示密钥
    codec = ServiceTokenCodec(JwtCodec(DEMO_SIGNING_KEY))

    # 1. 白名单内的服务：正常放行
    check("实时服务", codec, codec.issue(ServiceIdentity("RealTimeService")))
    # 2. 签名没问题，但是服务名不在白名单：拒绝
    check("不在白名单", codec, codec.issue(ServiceIdentity("GatewayService")))
    # 3. 手动修改签名字符，模拟抓包篡改token：拒绝
    check("篡改签名", codec, tamper(codec.issue(ServiceIdentity("RealTimeService"))))

    # 4. 用同一个密钥签发ACCESS用户令牌，尝试冒充SERVICE服务令牌
    user_token = JwtCodec(DEMO_SIGNING_KEY).encode(
        subject="RealTimeService",
        expires_in=timedelta(minutes=5),
        token_type=TokenType.ACCESS,
    )
    check("用户令牌冒充", codec, user_token)

    # 5. 有效期只有1秒，休眠2秒之后再校验，模拟过期token
    short_lived = ServiceTokenCodec(JwtCodec(DEMO_SIGNING_KEY), lifetime=timedelta(seconds=1))
    token = short_lived.issue(ServiceIdentity("RealTimeService"))
    time.sleep(2)
    check("已过期", short_lived, token)

if __name__ == "__main__":
    # 直接运行这个文件的时候才执行
    configure_logging(json_output=False)
    # 启动演示逻辑
    main()