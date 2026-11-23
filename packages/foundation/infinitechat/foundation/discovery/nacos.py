"""Nacos 2.1-compatible idempotent registration and discovery adapter."""
# Nacos2.1 兼容的幂等注册与服务发现适配器

import asyncio  # 导入异步IO库
import hashlib  # 导入哈希工具（本文件暂时未使用）
from collections.abc import Sequence  # 导入序列抽象类型

from infinitechat.foundation.discovery.models import ServiceInstance
# 导入服务实例模型
from infinitechat.foundation.settings import NacosSettings
# 导入Nacos配置类

from v2.nacos import (  # type: ignore[import-untyped]
    ClientConfigBuilder,
    DeregisterInstanceParam,
    ListInstanceParam,
    NacosNamingService,
    RegisterInstanceParam,
)
# 导入nacos sdk相关类，忽略类型检查提示

# Nacos 服务注册发现
class NacosRegistry:
    # 初始化
    def __init__(self, client: NacosNamingService) -> None:  # 构造方法，注入nacos命名服务客户端
        self._client = client  # 保存nacos客户端实例
        # 本地缓存：key为注册唯一键，value是服务实例，用来做幂等判断
        self._registered: dict[tuple[str, str, int, str, str], ServiceInstance] = {}
        self._lock = asyncio.Lock()  # 异步锁，防止并发注册/注销冲突
        self._closed = False  # 标记客户端是否已经关闭

    # 注册服务实例
    async def register(self, instance: ServiceInstance) -> bool:
        async with self._lock:  # 加异步锁，代码块结束自动释放锁
            self._ensure_open()  # 校验当前适配器未关闭
            key = instance.registration_key  # 获取实例的唯一注册key
            if key in self._registered:  # 本地缓存已经存在，说明已经注册过
                return False
            registered = await self._client.register_instance(  # 调用nacos sdk执行注册
                RegisterInstanceParam(
                    service_name=instance.service_name,  # 服务名称
                    ip=instance.ip,  # 实例ip
                    port=instance.port,  # 实例端口
                    metadata=dict(instance.metadata),  # 实例元数据，转成普通字典
                    cluster_name=instance.cluster_name,  # 集群名
                    group_name=instance.group_name,  # 分组名
                    enabled=instance.enabled,  # 是否启用
                    healthy=instance.healthy,  # 是否健康
                    ephemeral=instance.ephemeral,  # 是否临时节点
                )
            )
            if not registered:  # nacos返回注册失败
                raise RuntimeError("Nacos rejected service registration")
            self._registered[key] = instance  # 写入本地缓存，标记已注册
            return True

    # 注销服务实例
    async def deregister(self, instance: ServiceInstance) -> bool:
        async with self._lock:  # 加异步锁，代码块结束自动释放锁
            self._ensure_open()  # 校验当前适配器未关闭
            key = instance.registration_key  # 获取实例的唯一注册key
            if key not in self._registered:  # 本地缓存不存在，无需注销
                return False
            deregistered = await self._client.deregister_instance(self._deregister_param(instance))  # 调用sdk注销
            if not deregistered:  # nacos返回注销失败
                raise RuntimeError("Nacos rejected service deregistration")
            self._registered.pop(key, None)  # 从本地缓存删除该实例记录
            return True
            
            
    # 发现服务实例
    async def discover(
        self,
        service_name: str,  # 要查询的服务名
        *,  # 标记后面参数必须关键字传参
        group_name: str = "DEFAULT_GROUP",  # 服务分组，默认DEFAULT_GROUP
        cluster_names: Sequence[str] = (),  # 指定集群列表，空代表全部集群
        healthy_only: bool = True,  # 是否只返回健康实例，默认开启
        subscribe: bool = True,  # 是否订阅服务变更，开启后本地缓存更新
    ) -> tuple[ServiceInstance, ...]:
        self._ensure_open()  # 校验适配器未关闭
        instances = await self._client.list_instances(  # 调用nacos sdk拉取实例列表
            ListInstanceParam(
                service_name=service_name,  # 传入服务名称
                group_name=group_name,  # 传入分组名称
                clusters=list(cluster_names),  # 集群转成list传给sdk
                subscribe=subscribe,  # 是否订阅变更
                healthy_only=healthy_only,  # 是否只查健康节点
            )
        )
        return tuple(
            ServiceInstance(  # 封装成内部统一的ServiceInstance模型
                service_name=service_name,  # 服务名
                ip=instance.ip,  # 实例ip
                port=instance.port,  # 实例端口
                metadata=instance.metadata,  # 实例元数据
                cluster_name=instance.clusterName,  # 集群名称
                group_name=group_name,  # 分组名称
                healthy=instance.healthy,  # 健康标记
                enabled=instance.enabled,  # 是否启用
                ephemeral=instance.ephemeral,  # 是否临时节点
            )
            for instance in instances
            if instance.enabled and (instance.healthy or not healthy_only)  # 过滤启用，配合healthy_only规则
        )

    # 按路由键选择实例
    @staticmethod  # 静态方法，不需要实例self
    def choose_instance(
        instances: Sequence[ServiceInstance],  # 候选实例列表
        *,  # 标记后面参数必须关键字传参
        routing_key: str,  # 路由key，用来做一致性哈希
    ) -> ServiceInstance:
        eligible = tuple(
            instance  # 筛选符合条件的实例
            for instance in instances
            if instance.healthy and instance.enabled
        )
        if not eligible:  # 没有可用实例
            raise LookupError("no healthy service instance is available")
        digest = hashlib.sha256(routing_key.encode()).digest()  # 对路由key做sha256哈希
        index = int.from_bytes(digest[:8], "big") % len(eligible)  # 取前8字节转大整数，对实例数量取模
        return eligible[index]  # 返回选中的实例
        
            
    # 这个是最后的        
    def _ensure_open(self) -> None:
        if self._closed:  # 判断适配器是否已经关闭
            raise RuntimeError("Nacos registry is closed")  # 已关闭则抛出运行时异常