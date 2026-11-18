"""结构化日志，自动带上链路追踪ID，敏感信息自动脱敏"""

# 导入标准日志库
import logging
# 导入映射类型，用来判断字典
from collections.abc import Mapping, MutableMapping
# 任意类型注解
from typing import Any

# 导入结构化日志库 structlog
import structlog

# 导入拿当前请求上下文的函数，用来获取request_id、trace_id
from infinitechat.foundation.context import current_request_context

# 敏感字段黑名单，下面这些都是鉴权、登录、加密凭证，禁止明文打印到日志
# access_token：访问令牌，调用业务接口的临时通行证
# api_key：第三方接口密钥，用来调用短信、AI、对象存储等付费服务
# authorization：请求头鉴权字段，一般存放 Bearer token
# captcha：验证码，短信/图形验证码
# code：OAuth授权码，一次性凭证，用来换取access_token
# credential：通用凭据，账号、密钥类凭证统称
# jwt：JSON Web Token，常用的用户身份令牌
# password：用户密码
# refresh_token：刷新令牌，access_token过期后，用它换新令牌
# secret：通用加密/签名密钥
# secret_key：密钥，很多框架、SDK的密钥字段名
# token：通用令牌，各类身份、临时凭证统称
_SENSITIVE_KEYS = frozenset({
    "access_token",
    "api_key",
    "authorization",
    "captcha",
    "code",
    "credential",
    "jwt",
    "password",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
})


# 判断这个key是不是敏感字段
def _is_sensitive_key(key: str) -> bool:
    # 转小写，把横杠换成下划线，统一格式，方便匹配
    normalized = key.lower().replace("-", "_")
    # 要么直接命中黑名单，要么名字以敏感后缀结尾，比如 xxx_password、xxx_token
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_credential", "_password", "_secret", "_secret_key", "_token")
    )


# 递归脱敏函数，碰到敏感内容就替换成 [REDACTED]
def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    # 普通值，不是字典、列表，直接原样返回
    return value


# 脱敏敏感字段，structlog 处理器
def redact_sensitive_values(
    _logger: Any,  # structlog固定入参，这里不用，加下划线标记
    _method_name: str,  # structlog固定入参，这里不用，加下划线标记
    event_dict: MutableMapping[str, Any],  # 日志原始数据字典
) -> MutableMapping[str, Any]:
    redacted = _redact(event_dict)
    # 防御判断：脱敏之后必须还是可修改字典，防止异常
    if not isinstance(redacted, MutableMapping):
        raise TypeError("structured log event must be a mutable mapping")
    return redacted

# 注入请求上下文到日志，structlog 处理器
def add_request_context(
    _logger: Any,  # structlog固定入参，这里不用
    _method_name: str,  # structlog固定入参，这里不用
    event_dict: MutableMapping[str, Any],  # 日志原始数据字典
) -> MutableMapping[str, Any]:
    # 获取当前协程的请求追踪上下文
    context = current_request_context()
    if context is not None:
        # 只有日志里没有request_id时，才填充，不覆盖用户手动传的值
        event_dict.setdefault("request_id", context.request_id)
        # 只有日志里没有correlation_id时，才填充
        event_dict.setdefault("correlation_id", context.correlation_id)
        # trace_id是可选的，有值才去填充
        if context.trace_id:
            event_dict.setdefault("trace_id", context.trace_id)
    return event_dict


# 配置结构化日志
def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    # 选择渲染器：json输出就用JSON渲染，本地调试用控制台渲染
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    # 公共处理器流水线，日志会按这个顺序依次处理
    shared_processors: list[structlog.types.Processor] = [
        # 合并contextvars里面的上下文变量
        structlog.contextvars.merge_contextvars,
        # 把request_id、correlation_id、trace_id打进日志
        add_request_context,
        # 对日志里敏感字段做脱敏
        redact_sensitive_values,
        # 自动添加日志级别字段，比如INFO、ERROR
        structlog.processors.add_log_level,
        # 加上UTC时间戳，格式是iso标准格式
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    # 初始化标准logging库，只保留message内容，强制覆盖旧配置
    logging.basicConfig(level=level, format="%(message)s", force=True)
    # structlog全局配置
    structlog.configure(
        # 处理器列表：先跑上面公共流程，最后交给渲染器输出
        processors=[*shared_processors, renderer],
        # 日志过滤包装类，低于设置level的日志直接丢弃
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        # 用标准logging的日志工厂，兼容原生logging
        logger_factory=structlog.stdlib.LoggerFactory(),
        # 第一次拿到logger之后缓存，不用反复创建
        cache_logger_on_first_use=True,
    )
