"""请求与跨协议追踪上下文，用来传递 request_id、trace_id 链路信息"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

# 请求关联上下文对象
@dataclass(frozen=True, slots=True)  # 不可变、启用slots节省内存，适合上下文只读数据
class RequestContext:
    request_id: str  # 单次请求独立ID
    correlation_id: str  # 跨服务、跨消息的关联ID，串联整条业务链路
    trace_id: str | None = None  # OpenTelemetry链路追踪ID，可选

# 全局上下文变量，跟着协程/请求隔离，不会串数据
_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "infinitechat_request_context",
    default=None,
)


# 规范化上下文ID
def normalize_context_id(value: str | None) -> str:
    """处理校验链路ID，空值就自动生成uuid，非法格式直接抛异常"""
    # 有输入就去掉首尾空格，没有输入就置空字符串
    candidate = value.strip() if value else ""
    # 空内容，自动生成一个uuid返回
    if not candidate:
        return str(uuid4())
    # 长度超过128 或者 里面包含空格，直接报错
    if len(candidate) > 128 or any(character.isspace() for character in candidate):
        raise ValueError(
            "context ID must be non-empty, at most 128 characters, and contain no spaces"
        )
    # 校验通过，返回清理后的ID
    return candidate

# 绑定当前请求上下文
@contextmanager
def bind_request_context(
    *,  # 强制后面参数只能用关键字传参，防止传参顺序搞混
    request_id: str | None = None,  # 请求ID，不传就自动生成
    correlation_id: str | None = None,  # 跨服务关联ID，不传默认和request_id一致
    trace_id: str | None = None,  # OTEL链路追踪ID，可选
) -> Iterator[RequestContext]:
    """
    上下文管理器，绑定请求追踪信息。
    进入代码块时挂载上下文，退出自动清理，不会污染别的协程。
    """
    # 先规范化校验request_id，空值自动生成uuid
    normalized_request_id = normalize_context_id(request_id)
    # correlation_id如果没传，就复用request_id
    context = RequestContext(
        request_id=normalized_request_id,
        correlation_id=normalize_context_id(correlation_id or normalized_request_id),
        trace_id=trace_id,
    )
    # 把上下文塞进ContextVar，拿到token用于后续重置
    token = _REQUEST_CONTEXT.set(context)
    try:
        yield context  # 把上下文交给with里面的代码使用
    finally:
        # 不管里面代码正常跑完还是报错，一定清空上下文，防止串数据
        _REQUEST_CONTEXT.reset(token)

# 获取当前请求上下文
def current_request_context() -> RequestContext | None:
    """拿到当前协程里的请求追踪上下文，没有就返回None"""
    return _REQUEST_CONTEXT.get()

def correlation_headers() -> dict[str, str]:
    """
    取出当前上下文的ID，封装成HTTP请求头。
    调用下游服务时带上这两个头，实现跨服务链路串联。
    """
    context = current_request_context()
    if context is None:
        return {}
    return {
        "X-Request-Id": context.request_id,
        "X-Correlation-Id": context.correlation_id,
    }