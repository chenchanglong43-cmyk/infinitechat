"""请求 ID 守门演示.

空值换成新 UUID, 含空白或超过 128 个字符直接拒绝, 合法值原样保留.
前置条件: 纯函数, 无需密钥/中间件.
"""

import logging  # 标准日志

from infinitechat.foundation.context import (
    bind_request_context,
    correlation_headers,
    current_request_context,
    normalize_context_id,
)

logger = logging.getLogger(__name__)

# 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
def check(case: str, raw: str | None) -> None:
    """喂一个外部传来的请求 ID, 打出它是被收下还是被拒."""
    try:  # 下面这段可能出错, 出错交给 except 处理
        value = normalize_context_id(raw)
    except ValueError as error:
        # 打一条 warning 日志
        logger.warning("请求 ID 被拒 case=%s err=%s", case, type(error).__name__)
        return
    # 打一条 info 日志
    logger.info("请求 ID 收下 case=%s length=%s kept=%s", case, len(value), value == raw)

# 六种输入各跑一遍，再确认上下文只在 with 块里有效
def main() -> None:
    """六种输入各跑一遍, 再看上下文只在 with 块里可见."""
    # 1. 合法的原样保留, 空串和 None 换成新的 UUID
    check("合法", "req-20261112-0001")  # 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
    check("空串", "")  # 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
    check("None", None)  # 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
    # 2. 带换行, 带空格, 超过 128 个字符的一律拒绝
    # 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
    check("换行伪造", "req-1\nlevel=error msg=转账成功")
    check("带空格", "req 1")  # 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
    check("超长", "x" * 129)  # 喂一个外部传来的请求 ID, 打出它是被收下还是被拒
    # 3. 绑定只在 with 块里生效, 出了块自动还原
    # 绑定请求上下文 context, 用完自动收尾
    with bind_request_context(request_id="req-20261112-0001") as context:
        logger.info(  # 打一条 info 日志
            "块内 request_id=%s correlation_id=%s headers=%s",
            context.request_id,
            context.correlation_id,
            sorted(correlation_headers()),
        )
    logger.info("块外 context=%s", current_request_context())  # 打一条 info 日志

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")  # 配置标准日志
    main()  # 六种输入各跑一遍, 再看上下文只在 with 块里可见