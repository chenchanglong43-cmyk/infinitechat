"""密码存储演示.

新密码一律存 argon2; 旧的加盐 MD5 还能登录, 登录成功时给出升级用的新哈希;
账号不存在时也做一次差不多耗时的校验.
前置条件: 纯函数, 无需密钥/中间件.
"""

import hashlib  # 哈希算法
import time  # 时间
from collections.abc import Callable

import structlog  # 结构化日志

from infinitechat.foundation.telemetry import configure_logging
from pydantic import SecretStr

from user_service.auth.passwords import PasswordManager

logger = structlog.get_logger(__name__)

# 演示用的旧盐值, 口令和一个错误口令
LEGACY_SALT = "demo-legacy-salt"  # 常量 legacy盐: 演示用的旧盐值, 口令和一个错误口令
TYPED_IN = "Passw0rd!"  # 常量 typed in: 演示用的口令
WRONG_INPUT = "Wrong-0000"

# 跑一次 action
def elapsed_ms(action: Callable[[], object]) -> float:
    """跑一次 action, 返回花了多少毫秒."""
    started = time.perf_counter()
    action()  # 执行传进来的这个操作
    return (time.perf_counter() - started) * 1000

# 新旧两种哈希各验一遍
def main() -> None:
    """新旧两种哈希各验一遍, 再比较账号不存在时的耗时."""
    manager = PasswordManager(legacy_salt=SecretStr(LEGACY_SALT))
    # 1. 新密码存 argon2, 同一个口令每次哈希都不一样
    first = manager.hash_password(TYPED_IN)
    second = manager.hash_password(TYPED_IN)
    logger.info(  # 打一条 info 日志
        "新密码",
        scheme=first.split("$")[1],
        differs=first != second,
        valid=manager.verify(first, TYPED_IN).valid,
    )
    # 2. 旧格式是加盐 MD5: 照样能登录, 并给出 argon2 的升级哈希
    legacy = hashlib.md5((LEGACY_SALT + TYPED_IN).encode(), usedforsecurity=False).hexdigest()
    result = manager.verify(legacy, TYPED_IN)
    upgraded = (result.upgraded_hash or "").startswith("$argon2")
    logger.info("旧哈希登录", valid=result.valid, upgraded=upgraded)  # 打一条 info 日志
    # 3. 口令不对, 新旧两种格式都拒
    logger.warning(  # 打一条 warning 日志
        "口令错误",
        argon2_valid=manager.verify(first, WRONG_INPUT).valid,
        legacy_valid=manager.verify(legacy, WRONG_INPUT).valid,
    )
    # 4. 账号不存在时也做一次 argon2 校验, 耗时和真校验在同一个量级
    real = elapsed_ms(lambda: manager.verify(first, WRONG_INPUT))
    dummy = elapsed_ms(lambda: manager.consume_dummy_verification(WRONG_INPUT))
    logger.info("耗时对比", comparable=0.3 < dummy / real < 3)  # 打一条 info 日志

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 新旧两种哈希各验一遍, 再比较账号不存在时的耗时