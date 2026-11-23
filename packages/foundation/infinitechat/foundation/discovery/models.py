"""Infrastructure-neutral service discovery values."""
# 基础设施无关的服务发现模型定义

from collections.abc import Mapping  # 导入映射抽象类型
from dataclasses import dataclass, field  # 导入数据类、字段工具
from types import MappingProxyType  # 导入只读字典包装器

# 服务实例描述
@dataclass(frozen=True, slots=True)  # 数据类：不可变、启用slots节省内存
class ServiceInstance:
    service_name: str  # 服务名称
    ip: str  # 实例IP地址
    port: int  # 实例端口号
    metadata: Mapping[str, str] = field(default_factory=dict)  # 附加元数据，默认空字典
    cluster_name: str = ""  # 集群名称，默认空字符串
    group_name: str = "DEFAULT_GROUP"  # 服务分组，默认DEFAULT_GROUP
    healthy: bool = True  # 健康状态，默认健康
    enabled: bool = True  # 是否启用，默认启用
    ephemeral: bool = True  # 是否临时节点，下线自动注销，默认true

    # __post_init__是 dataclass 自动调用的钩子： 每次创建ServiceInstance(...)实例时，字段赋值完成后立刻触发一次。
    def __post_init__(self) -> None:  # 数据类构造完成后的钩子，做参数校验
        if not self.service_name:  # 判断服务名是否为空
            raise ValueError("service_name must not be empty")  # 服务名不能为空，抛异常
        if not self.ip:  # 判断IP是否为空
            raise ValueError("service instance ip must not be empty")  # IP不能为空，抛异常
        if not 1 <= self.port <= 65_535:  # 判断端口是否在合法范围
            raise ValueError("service instance port is invalid")  # 端口非法，抛异常
        # frozen类不能直接赋值，用底层方法把metadata转为只读，防止外部修改
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    # 注册唯一键
    @property  # 只读属性，访问方式和字段一样
    def registration_key(self) -> tuple[str, str, int, str, str]:  # 唯一标识当前实例的元组
        return (
            self.service_name,
            self.ip,
            self.port,
            self.cluster_name,
            self.group_name,
        )