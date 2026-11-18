"""Common protocol-level Prometheus instruments; business metrics stay in services."""
# 通用协议层面Prometheus监控指标；业务专属指标写在各个service里面，不要放这里

# 导入dataclass，快速定义数据类
from dataclasses import dataclass
# 导入prometheus客户端组件：默认注册中心、自定义注册中心、计数器、直方图
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

# 基础协议指标封装
@dataclass(frozen=True, slots=True)
# frozen=True：对象创建完之后不能修改，防止指标被意外改掉
# slots=True：不用__dict__，省内存，速度更快
class FoundationMetrics:
    requests: Counter  # 入站请求计数器，统计进来的请求总次数
    request_duration: Histogram  # 入站请求耗时直方图，统计请求响应时间分布
    external_calls: Counter  # 外部依赖调用计数器，统计调用第三方、别的服务次数
    external_call_duration: Histogram  # 外部调用耗时直方图，统计依赖调用的耗时分布

    # 创建指标集合的类方法
    @classmethod
    def create(
        cls,  # cls代表当前这个FoundationMetrics类
        *,  # 星号：后面参数必须用关键字传参，不能按位置传
        registry: CollectorRegistry = REGISTRY,  # 指标注册中心，默认全局注册中心，测试可以单独传
        namespace: str = "infinitechat",  # 指标命名空间，用来区分项目，默认infinitechat
    ) -> "FoundationMetrics":
        return cls(  # 实例化FoundationMetrics，把四个指标传进去
            requests=Counter(  # 创建请求计数器
                "requests_total",  # 指标名称，规范结尾_total
                "Total inbound protocol requests",  # 指标说明：总的入站协议请求
                ("service", "protocol", "operation", "outcome"),  # 标签：服务、协议、操作、结果
                namespace=namespace,  # 绑定命名空间
                registry=registry,  # 绑定注册中心
            ),
            request_duration=Histogram(  # 创建请求耗时直方图
                "request_duration_seconds",  # 指标名，单位秒
                "Inbound protocol request duration",  # 说明：入站请求耗时
                ("service", "protocol", "operation"),  # 标签：服务、协议、操作
                namespace=namespace,
                registry=registry,
            ),
            external_calls=Counter(  # 创建外部依赖调用计数器
                "external_calls_total",  # 指标名称
                "Total outbound dependency calls",  # 说明：向外调用依赖总次数
                ("service", "dependency", "operation", "outcome"),  # 标签：服务、依赖名、操作、结果
                namespace=namespace,
                registry=registry,
            ),
            external_call_duration=Histogram(  # 创建外部调用耗时直方图
                "external_call_duration_seconds",  # 指标名称，单位秒
                "Outbound dependency call duration",  # 说明：向外调用依赖的耗时
                ("service", "dependency", "operation"),  # 标签：服务、依赖名、操作
                namespace=namespace,
                registry=registry,
            ),
        )