"""雪花 ID 演示.

同一毫秒内严格递增, 能反解出时间和节点; 时钟回拨和越界的节点号直接报错.
前置条件: 纯函数, 无需密钥/中间件.
"""

import structlog  # 结构化日志

from infinitechat.foundation.ids import ClockRollbackError, SnowflakeGenerator

from infinitechat.foundation.telemetry import configure_logging

logger = structlog.get_logger(__name__)

# 手动拨动的时钟
class ManualClock:
    """手动拨动的时钟: 想让时间停在哪一毫秒, 就停在哪一毫秒."""

    def __init__(self, now_ms: int) -> None:  # 构造方法: 创建对象时把依赖存起来
        """记下起始的毫秒数."""
        self.now_ms = now_ms

    def __call__(self) -> int:  # 让对象能像函数一样调用
        """生成器每次发号前都来问一次现在几点."""
        return self.now_ms

# 同一毫秒连发五个, 真实时钟连发一万个
def main() -> None:
    """同一毫秒连发五个, 真实时钟连发一万个, 再把时钟往回拨."""
    # 1. 时间停在同一毫秒: 序号从 0 开始逐个加一, ID 跟着递增
    clock = ManualClock(1_762_900_000_000)
    generator = SnowflakeGenerator(worker_id=3, data_center_id=1, clock=clock)
    ids = [generator.next_id() for _ in range(5)]  # ID: 生成一串数字里每个算出的 生成下一个 ID
    last = generator.decode(ids[-1])
    logger.info(  # 打一条 info 日志
        "同一毫秒",
        increasing=ids == sorted(set(ids)),
        sequences=[generator.decode(value).sequence for value in ids],
        data_center=last.data_center_id,
        worker=last.worker_id,
        same_ms=last.timestamp_ms == clock.now_ms,
    )
    # 2. 真实时钟连发一万个: 不重复, 严格递增, 位数放得进 BIGINT
    real = SnowflakeGenerator(worker_id=3, data_center_id=1)
    batch = [real.next_id() for _ in range(10_000)]  # 批量: 生成一串数字里每个算出的 生成下一个 ID
    logger.info(  # 打一条 info 日志
        "真实时钟一万个",
        unique=len(set(batch)),
        increasing=batch == sorted(batch),
        bits=batch[-1].bit_length(),
    )
    # 3. 时钟往回拨 5 毫秒: 宁可报错, 也不发出可能重复的 ID
    clock.now_ms -= 5  # 时钟的当前毫秒时间 减去 5
    try:  # 下面这段可能出错, 出错交给 except 处理
        generator.next_id()
    except ClockRollbackError as error:
        logger.warning("时钟回拨被拒", error=type(error).__name__)  # 打一条 warning 日志
    # 4. 机器号只占 5 位, 32 在构造时就被挡下
    try:  # 下面这段可能出错, 出错交给 except 处理
        SnowflakeGenerator(worker_id=32, data_center_id=1)  # 创建Snowflake ID 生成器
    except ValueError as error:
        logger.warning("机器号越界被拒", error=type(error).__name__)  # 打一条 warning 日志

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 同一毫秒连发五个, 真实时钟连发一万个, 再把时钟往回拨