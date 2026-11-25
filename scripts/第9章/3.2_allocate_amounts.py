"""金额分配演示.

金额一律按分做整数运算: 普通红包均分, 除不尽的零头补给前几个人; 拼手气红包每份至少 1 分,
加起来一分不差; 总额不够每人 1 分直接拒绝.
前置条件: 纯函数, 无需密钥/中间件.
"""

import random  # 随机数

import structlog  # 结构化日志

from infinitechat.foundation.telemetry import configure_logging

from red_packet_service.allocation import allocate_normal, allocate_random

logger = structlog.get_logger(__name__)

# 分几种典型的红包, 再连抽一千次拼手气
def main() -> None:
    """分几种典型的红包, 再连抽一千次拼手气."""
    # 1. 普通红包: 1 元分给 3 个人, 零头 1 分补给第一个人
    normal = allocate_normal(100, 3)
    logger.info("普通红包", amounts=normal, total=sum(normal))  # 打一条 info 日志
    # 2. 拼手气红包: 固定随机种子, 结果可以复现
    lucky = allocate_random(100, 5, rng=random.Random(42))  # noqa: S311 - 固定种子便于复现
    logger.info("拼手气红包", amounts=lucky, total=sum(lucky))  # 打一条 info 日志
    # 3. 连抽一千次: 每次总额都对, 每份至少 1 分
    rng = random.Random(7)  # noqa: S311 - 固定种子便于复现
    # trials: 生成一串数字里每个算出的 拼手气红包:切分随机金额后打乱
    trials = [allocate_random(1000, 10, rng=rng) for _ in range(1000)]
    logger.info(  # 打一条 info 日志
        "一千次拼手气",
        all_conserved=all(sum(trial) == 1000 for trial in trials),
        min_share=min(min(trial) for trial in trials),
        max_share=max(max(trial) for trial in trials),
    )
    # 4. 总额刚好每人 1 分: 没有可以随机的余量, 退回均分
    logger.info("刚好每人 1 分", amounts=allocate_random(5, 5))  # 打一条 info 日志
    # 5. 总额不够每人 1 分
    try:  # 下面这段可能出错, 出错交给 except 处理
        allocate_normal(3, 5)  # 普通红包均分(余数补给前几个)
    except ValueError as error:
        logger.warning("总额不够", error=str(error))  # 打一条 warning 日志

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式
    main()  # 分几种典型的红包, 再连抽一千次拼手气