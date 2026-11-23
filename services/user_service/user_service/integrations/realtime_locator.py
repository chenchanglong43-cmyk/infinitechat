"""Resolve the correct WebSocket endpoint from Nacos metadata."""
# 从Nacos元数据解析出正确的WebSocket连接地址

from infinitechat.foundation.discovery import NacosRegistry
# 导入nacos注册发现适配器

# 实时地址定位
class RealtimeLocator:
    # 初始化
    def __init__(self, registry: NacosRegistry) -> None:  # 构造方法，注入nacos注册器
        self._registry = registry  # 保存注册发现实例

    # 定位实时地址
    async def locate(self, routing_key: str) -> str:
        instances = await self._registry.discover("RealTimeService")  # 拉取RealTimeService服务实例列表
        instance = self._registry.choose_instance(instances, routing_key=routing_key)  # 根据路由键哈希选一个实例
        public_url = (instance.metadata.get("wsPublicUrl") or "").strip()  # 优先读取元数据里的公网ws地址
        if public_url:
            # 只返回 主机:端口/路径, ws 还是 wss 由前端按页面协议决定
            return public_url.split("://", 1)[-1].rstrip("/")  # 去掉协议头，末尾去掉斜杠
        ws_port = instance.metadata.get("wsPort")  # 读取ws端口元数据
        ws_path = instance.metadata.get("wsPath", "/ws/netty")  # 读取ws路径，默认 /ws/netty
        if not ws_port or not ws_port.isdigit():
            # RealTimeService instance has no valid wsPort metadata
            raise LookupError("RealTimeService instance has no valid wsPort metadata")  # 端口非法抛异常
        if not ws_path.startswith("/"):
            # RealTimeService instance has no valid wsPath metadata
            raise LookupError("RealTimeService instance has no valid wsPath metadata")  # 路径必须以/开头
        return f"{instance.ip}:{int(ws_port)}{ws_path}"  # 拼接ip+端口+路径返回地址