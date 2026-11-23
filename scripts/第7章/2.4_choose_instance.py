"""实例选择演示.

同一个路由键总落到同一个实例; 不健康的实例不参与选择; 一个可用实例都没有就报错;
实例数一变, 大部分路由键会换到别的实例上.
前置条件: 纯函数, 无需密钥/中间件.
"""

from collections import Counter  # 导入计数器，用来统计实例分配数量
from dataclasses import replace  # 导入dataclass替换字段工具

import structlog  # 导入结构化日志库

from infinitechat.foundation.discovery import NacosRegistry, ServiceInstance
# 导入nacos注册器、服务实例模型

from infinitechat.foundation.telemetry import configure_logging
# 导入日志初始化工具

logger = structlog.get_logger(__name__)  # 获取日志实例

# 常量 键: 生成一串数字里每个用户ID算出的 转成字符串
KEYS = [str(user_id) for user_id in range(1, 3001)]  # 生成3000个路由key，1~3000字符串

# 造一组实时服务实例, IP 从 10.0.0.1 开始
def instances(count: int) -> list[ServiceInstance]:
    """造一组实时服务实例, IP 从 10.0.0.1 开始."""
    return [
        # 创建服务实例描述
        ServiceInstance(service_name="RealTimeService", ip=f"10.0.0.{index + 1}", port=8102)
        for index in range(count)
    ]

# 给每个路由键选一个实例
def assign(pool: list[ServiceInstance]) -> dict[str, str]:
    """给每个路由键选一个实例, 返回 路由键 -> 实例 IP."""
    return {key: NacosRegistry.choose_instance(pool, routing_key=key).ip for key in KEYS}

# 三千个用户 ID 当路由键, 看分布
def main() -> None:
    """三千个用户 ID 当路由键, 看分布, 稳定性和实例数变化的影响."""
    three = instances(3)  # 构造3个服务实例
    first = assign(three)  # 3000个key分配到3个实例，保存分配结果
    # 1. 同一个键每次都落到同一个实例, 三个实例大致平分
    counts = sorted(Counter(first.values()).values())  # 统计每个实例分到多少key，排序
    logger.info("稳定且均匀", stable=first == assign(three), counts=counts)  # 打一条 info 日志
    # 2. 第二个实例不健康: 不再被选中
    degraded = [three[0], replace(three[1], healthy=False), three[2]]  # 把第二个实例标记为不健康
    logger.info("排除不健康实例", chosen=sorted(set(assign(degraded).values())))  # 打一条 info 日志
    # 3. 一个可用实例都没有
    try:  # 下面这段可能出错, 出错交给 except 处理
        # 按路由键选择实例
        NacosRegistry.choose_instance([replace(three[0], enabled=False)], routing_key="1")
    except LookupError as error:
        logger.warning("没有可用实例", error=type(error).__name__)  # 打一条 warning 日志
    # 4. 扩容到四个实例: 按哈希取模分配, 大部分键会换实例
    moved = sum(first[key] != new for key, new in assign(instances(4)).items())  # 统计分配发生变化的key数量
    logger.info("扩容到四个实例", moved=moved, total=len(KEYS))  # 打一条 info 日志
    # 5. 实例描述在构造时就校验端口
    try:  # 下面这段可能出错, 出错交给 except 处理
        ServiceInstance(service_name="RealTimeService", ip="10.0.0.9", port=0)  # 创建服务实例描述，端口0非法
    except ValueError as error:
        logger.warning("端口非法", error=type(error).__name__)  # 打一条 warning 日志

if __name__ == "__main__":  # 直接运行这个文件时才执行下面的代码
    configure_logging(json_output=False)  # 配置日志输出格式，关闭json格式
    main()  # 三千个用户 ID 当路由键, 看分布, 稳定性和实例数变化的影响