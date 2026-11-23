"""Complete P0 public route map and Nacos-backed upstream resolution."""
# 完整P0级公开路由映射，基于Nacos解析上游服务地址

from dataclasses import dataclass  # 导入dataclass装饰器

from infinitechat.foundation.discovery import NacosRegistry  # 导入Nacos注册发现工具

# 路由目标
@dataclass(frozen=True, slots=True)  # 不可变数据类，开启slots节省内存
class RouteTarget:
    prefix: str  # 路由前缀
    service_name: str  # 对应的后端服务名称

ROUTE_TARGETS: tuple[RouteTarget, ...] = (  # 路由目标元组，一共5条
    RouteTarget("/api/chat/redPacket", "RedPacketService"),
    RouteTarget("/api/contact", "UserService"),
    RouteTarget("/api/message", "OfflineDataService"),
    RouteTarget("/api/group", "UserService"),
    RouteTarget("/api/user", "UserService"),
)

PUBLIC_AUTH_EXEMPT_PATHS = frozenset(  # 公开免鉴权路径集合，不可修改
    {
        "/api/user/login/code",
        "/api/user/register",
        "/api/user/sendCaptcha",
        "/api/user/login/password",
        "/api/user/refresh",
    }
)

# 是否免认证
def is_public_auth_exempt(path: str) -> bool:
    return path in PUBLIC_AUTH_EXEMPT_PATHS  # 判断路径是否在免鉴权集合里

# 匹配路由目标
def target_for_path(path: str) -> RouteTarget | None:
    # 路径包含//、.、.. 时直接拒绝转发，防止路径穿越绕过校验
    if "//" in path or any(segment in {".", ".."} for segment in path.split("/")):
        return None
    # 内部接口不对外转发
    if path == "/api/internal" or path.startswith("/api/internal/"):
        return None
    # 按前缀匹配路由目标，命中就返回，没有匹配返回None
    return next(
        (
            target
            for target in ROUTE_TARGETS
            if path == target.prefix or path.startswith(f"{target.prefix}/")
        ),
        None,
    )

# Nacos上游解析
class NacosUpstreamResolver:
    # 初始化
    def __init__(self, registry: NacosRegistry) -> None:  # 注入Nacos注册发现实例
        self._registry = registry

    # 解析上游地址
    async def resolve(self, service_name: str, *, routing_key: str) -> str:
        instances = await self._registry.discover(service_name)  # 拉取该服务所有实例
        instance = self._registry.choose_instance(instances, routing_key=routing_key)  # 根据路由键选实例
        http_port = instance.metadata.get("httpPort", str(instance.port))  # 优先取元数据里的http端口，兜底用实例port
        if not http_port.isdigit():  # 端口不是合法数字，抛出异常
            raise LookupError(f"{service_name} has invalid httpPort metadata")
        return f"http://{instance.ip}:{int(http_port)}"  # 拼接成完整上游http地址返回