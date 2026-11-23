"""Service registration and discovery boundaries."""

from infinitechat.foundation.discovery.models import ServiceInstance

from infinitechat.foundation.discovery.nacos import NacosRegistry, create_nacos_registry

__all__ = ("NacosRegistry", "ServiceInstance", "create_nacos_registry")