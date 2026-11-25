"""Internal HTTP client from the realtime service to UserService."""

# 异步HTTP客户端，用来调别的服务
import httpx
# Nacos服务注册发现工具
from infinitechat.foundation.discovery import NacosRegistry
# 服务间鉴权：生成、解析服务token
from infinitechat.foundation.security import ServiceIdentity, ServiceTokenCodec


# Realtime服务调用下游的客户端封装
class RealtimeClients:
    def __init__(
        self,
        registry: NacosRegistry,       # Nacos注册发现实例
        http: httpx.AsyncClient,        # 复用的异步http客户端（连接池）
        service_tokens: ServiceTokenCodec, # 服务token生成器
    ) -> None:
        self._registry = registry
        self._http = http
        self._tokens = service_tokens

    # 内部方法：根据服务名 + 路由key，拿到目标服务的地址
    async def _base(self, service: str, routing_key: str) -> str:
        # 从Nacos查出这个服务所有可用实例
        instances = await self._registry.discover(service)
        # 根据routing_key做一致性哈希，选其中一个实例
        instance = self._registry.choose_instance(instances, routing_key=routing_key)
        # 优先读metadata里的httpPort，没有就用实例默认port
        port = instance.metadata.get("httpPort", str(instance.port))
        # 拼接成 http://ip:port
        return f"http://{instance.ip}:{int(port)}"

    # 内部方法：生成服务间调用请求头，带上服务身份token
    def _headers(self) -> dict[str, str]:
        # 给UserService证明：是RealTimeService在调用它
        return {"X-Service-Token": self._tokens.issue(ServiceIdentity("RealTimeService"))}

    # 查询这个会话里面有哪些用户（接收人列表）
    async def members(self, session_id: int) -> list[int]:
        # 找UserService实例地址，用session_id做路由key
        base = await self._base("UserService", str(session_id))
        # GET请求，查会话接收者
        response = await self._http.get(
            f"{base}/api/user/get/receivers",
            params={"sessionId": session_id},
            headers=self._headers(),
        )
        # 如果返回4xx/5xx，直接抛异常
        response.raise_for_status()
        # 把返回的json数组转成int的用户id列表返回
        return [int(value) for value in response.json()]