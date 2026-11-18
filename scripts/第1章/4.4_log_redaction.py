"""日志脱敏演示.

敏感字段嵌套多深都打成 [REDACTED], 请求 ID 自动带进每一行.
前置条件: 纯函数, 无需密钥/中间件.
"""

import structlog  # 结构化日志

from infinitechat.foundation.context import bind_request_context

from infinitechat.foundation.telemetry.logging import configure_logging, redact_sensitive_values

logger = structlog.get_logger(__name__)

# 演示用的明文口令和令牌, 看它们会不会漏进日志
TYPED_IN = "P@ssw0rd!"  # 常量 typed in: 演示用的明文口令和令牌, 看它们会不会漏进日志
ISSUED = "at-xyz"

# 先直接调脱敏处理器看结果，再打一条真日志看效果
def main() -> None:
    """先直接调脱敏处理器看结果, 再打一条真日志看终端里长什么样."""
    # 1. 顶层, 嵌套字典, 列表里的敏感字段都得换掉, 普通字段原样保留
    event = {
        "event": "登录请求",  # "event": "登录请求"
        "email": "alice@example.com",  # "email": "alice@example.co..."
        "password": TYPED_IN,  # "password": typed in
        "profile": {"nickname": "alice", "refresh_token": "rt-abc"},  # "profile": 2 个键的字典
        "attempts": [{"captcha": "123456"}, {"api_key": "k-1"}],  # 列表里的敏感字段也要打码
        "Smtp-Password": "smtp-secret",  # "Smtp-Password": "smtp-secret"
    }
    redacted = redact_sensitive_values(None, "info", event)
    secrets = (TYPED_IN, "rt-abc", "123456", "k-1", "smtp-secret")  # secrets: 5 项的元组
    # leaked: secrets里每个值算出的 值
    leaked = [value for value in secrets if value in str(redacted)]
    # 打一条 info 日志
    logger.info("脱敏结果", leaked=leaked, nickname_kept=redacted["profile"]["nickname"] == "alice")
    # 2. 在请求上下文里打一条真日志, request_id 不用手写
    with bind_request_context(request_id="req-demo-42"):  # 绑定请求上下文, 用完自动收尾
        # 打一条 info 日志
        logger.info("用户登录", email="alice@example.com", password=TYPED_IN, access_token=ISSUED)

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 先直接调脱敏处理器看结果, 再打一条真日志看终端里长什么样