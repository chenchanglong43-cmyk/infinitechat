"""OpenTelemetry provider construction without hidden global mutation."""
# 构建OpenTelemetry链路追踪提供者，不偷偷修改全局变量

from opentelemetry.sdk.resources import (
    DEPLOYMENT_ENVIRONMENT,  # 部署环境标识key，比如prod、dev
    SERVICE_NAME,  # 服务名称标识key
    SERVICE_VERSION,  # 服务版本标识key
    Resource,  # 资源对象，用来挂载服务基础信息
)
# 导入追踪器提供者，链路追踪的核心对象
from opentelemetry.sdk.trace import TracerProvider


# 构建链路追踪提供者
def build_tracer_provider(
        *,  # 星号：后面参数必须关键字传参，不能按位置传
        service_name: str,  # 服务名称，例如 user-service
        service_version: str,  # 服务版本号，例如 v1.0.0
        environment: str,  # 部署环境，dev / test / prod
) -> TracerProvider:
    # 创建资源，把服务基础信息绑定进去
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,  # 填入服务名
            SERVICE_VERSION: service_version,  # 填入服务版本
            DEPLOYMENT_ENVIRONMENT: environment,  # 填入部署环境
        }
    )
    # 用资源信息生成并返回追踪提供者实例
    return TracerProvider(resource=resource)
