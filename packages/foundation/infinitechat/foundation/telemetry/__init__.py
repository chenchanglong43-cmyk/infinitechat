"""Logging, metrics and tracing initialization."""
# 日志、监控指标、链路追踪的统一初始化入口

# 导入日志模块：日志配置函数、敏感数据脱敏函数
from infinitechat.foundation.telemetry.logging import configure_logging, redact_sensitive_values

# 导入监控指标模块：基础指标封装类
from infinitechat.foundation.telemetry.metrics import FoundationMetrics

# 导入链路追踪模块：构建追踪提供者的函数
from infinitechat.foundation.telemetry.tracing import build_tracer_provider

# __all__：控制 from xxx import * 时能导出哪些名字
__all__ = (
    "FoundationMetrics",
    "build_tracer_provider",
    "configure_logging",
    "redact_sensitive_values",
)