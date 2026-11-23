"""路由契约表核对演示.

每条对外路由都能在网关找到下游, 而且和契约里写的服务一致; 免鉴权清单和契约里的
"不需要登录"完全对得上; 内部接口一条也不会被网关转发.
前置条件: 纯函数, 无需密钥/中间件.
"""

from collections import Counter  # 导入计数器，用来统计各服务路由数量

import structlog  # 导入结构化日志库

from gateway.routing import is_public_auth_exempt, target_for_path  # 导入网关路由匹配、免鉴权判断函数
from infinitechat.contracts.http.routes import HTTP_ROUTES  # 导入全部HTTP路由契约

from infinitechat.foundation.telemetry import configure_logging  # 导入日志初始化工具

logger = structlog.get_logger(__name__)  # 获取日志实例

# 契约里的服务简称, 对应网关里的服务名
SERVICE_NAMES = {
    "user": "UserService",  # 契约简写user → 网关注册服务名UserService
    "offline": "OfflineDataService",  # 契约简写offline → 网关注册服务名OfflineDataService
    "red_packet": "RedPacketService",  # 契约简写red_packet → 网关注册服务名RedPacketService
    "realtime": "RealTimeService",  # 契约简写realtime → 网关注册服务名RealTimeService
}

def main() -> None:
    """拿契约表逐条对照网关的路由表和免鉴权清单."""
    public = [route for route in HTTP_ROUTES if route.visibility == "public"]  # 筛选公开路由
    internal = [route for route in HTTP_ROUTES if route.visibility == "internal"]  # 筛选内部路由
    by_service = dict(sorted(Counter(route.service for route in public).items()))  # 统计每个服务下公开路由数量并排序
    logger.info("路由契约表", public=len(public), internal=len(internal), by_service=by_service)  # 输出路由统计信息

    # 1. 下游服务匹配错误的路由：找不到路由目标，或者目标服务名称和契约不一致
    wrong_target = [
        route.path
        for route in public
        if (target := target_for_path(route.path)) is None
        or target.service_name != SERVICE_NAMES[route.service]
    ]
    print(wrong_target,"wrong_target")
    # 2. 鉴权配置不匹配：契约要求鉴权，但网关免鉴权；或者契约免鉴权，网关却需要鉴权
    auth_mismatch = [
        route.path
        for route in public
        if route.auth_required == is_public_auth_exempt(route.path)
    ]
    # 3. 内部接口被网关意外匹配转发的路由（内部接口禁止网关转发）
    forwarded = [route.path for route in internal if target_for_path(route.path) is not None]
    exempt = sum(not route.auth_required for route in public)  # 统计公开路由里免鉴权的接口总数
    logger.info(
        "核对结果",
        wrong_target=wrong_target,
        auth_mismatch=auth_mismatch,
        internal_forwarded=forwarded,
        exempt=exempt,
    )

if __name__ == "__main__":  # 直接运行本文件时执行
    configure_logging(json_output=False)  # 配置日志，关闭json格式输出
    main()  # 执行路由契约核对逻辑