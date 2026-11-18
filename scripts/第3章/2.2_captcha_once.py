"""邮箱验证码演示.

有效期内重复申请被拒; 同一个验证码只能用一次, 用过之后才能再申请; 释放只认当时那个码.
前置条件: 需 Redis.
"""

import asyncio  # 异步编程
import uuid

import structlog  # 结构化日志

from infinitechat.foundation.settings import read_env  # 先读进程环境变量，再读 .env
from infinitechat.foundation.telemetry import configure_logging

from user_service.integrations.redis import UserRedis

logger = structlog.get_logger(__name__)

# 同一个邮箱连申请两次, 再拿错码
async def main() -> None:
    """同一个邮箱连申请两次, 再拿错码, 对码, 用过的码各试一次."""
    cache = UserRedis.from_url(read_env("REDIS_URL"))
    # 每次运行换一个邮箱, 脚本可以反复跑
    # 邮箱: 字符串 "captcha-{...的hex[切片]}@..."
    email = f"captcha-{uuid.uuid4().hex[:8]}@example.com"
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 1. 第一次申请占到位置, 有效期内第二次申请被拒
        # 打一条 info 日志
        logger.info("第一次申请", reserved=await cache.reserve_captcha(email, "111111"))
        # 打一条 warning 日志
        logger.warning("有效期内再申请", reserved=await cache.reserve_captcha(email, "222222"))
        # 2. 错码不消耗, 对码消耗一次, 同一个码再用被拒
        # 打一条 warning 日志
        logger.warning("错误的码", consumed=await cache.consume_captcha(email, "999999"))
        # 打一条 info 日志
        logger.info("正确的码", consumed=await cache.consume_captcha(email, "111111"))
        # 打一条 warning 日志
        logger.warning("同一个码再用", consumed=await cache.consume_captcha(email, "111111"))
        # 3. 用过之后可以重新申请; 释放时只认当时发出去的那个码
        # 打一条 info 日志
        logger.info("用过之后再申请", reserved=await cache.reserve_captcha(email, "333333"))
        # 打一条 warning 日志
        logger.warning("拿别的码去释放", released=await cache.release_captcha(email, "000000"))
        # 打一条 info 日志
        logger.info("拿原码释放", released=await cache.release_captcha(email, "333333"))
    finally:  # 不管成功还是出错, 最后都执行这里
        await cache.aclose()

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    asyncio.run(main())  # 启动事件循环跑这个协程: 同一个邮箱连申请两次