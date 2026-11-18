"""Environment-backed, secret-safe shared settings."""

import os  # 读进程环境变量要用
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values  # pydantic-settings 自带的依赖，用来单独读 .env 里的某一项
from pydantic import Field, SecretStr

from pydantic_settings import BaseSettings, SettingsConfigDict

# 环境文件的位置：相对当前工作目录，也就是仓库根目录（课程里的命令都在仓库根执行）
ENV_FILE = ".env"

# 读单个环境变量：给演示脚本、迁移脚本这种不建配置类的地方用
def read_env(name: str, default: str | None = None) -> str:
    """先读进程环境变量，再读 .env；都没有就用默认值，连默认值也没有就报错。"""
    value = os.environ.get(name)  # 1. 进程环境变量优先，方便临时覆盖某一项
    if value is None:
        value = dotenv_values(ENV_FILE).get(name)  # 2. 再去 .env 文件里找
    if value is None:
        if default is None:
            # 3. 两处都没有，又没给默认值：直接报错，并告诉使用者去哪里配
            raise KeyError(f"缺少环境变量 {name}：请在仓库根目录的 .env 里配置")
        return default
    return value

# 共享配置基类
# 共享配置基类 (配置类, 字段从环境变量读)
class FoundationSettings(BaseSettings):
    """基础配置类：先读进程环境变量，再读仓库根目录的 .env 文件"""

    model_config = SettingsConfigDict(
        case_sensitive=False,  # 环境变量不区分大小写，ABC 和 abc 当成同一个
        env_file=ENV_FILE,  # 自动读取仓库根目录的 .env；进程里设了同名环境变量时以环境变量为准
        env_file_encoding="utf-8",  # .env 按 UTF-8 读，里面写中文注释也不会乱码
        extra="ignore",  # 遇到没定义的环境变量，直接忽略，不报错
        validate_default=True,  # 就算用默认值，也要跑一遍校验，防止默认值非法
    )

# 服务基础配置
# 服务基础配置 (配置类, 字段从环境变量读)
class ServiceSettings(FoundationSettings):
    app_env: str = "local"  # 应用环境：默认 "local"
    service_name: str  # 服务名
    # 监听地址：默认 "0.0.0.0"
    service_host: str = "0.0.0.0"  # noqa: S104 - bind address is explicit configuration
    # 服务端口：必须大于 0，不能大于 65535
    service_port: int = Field(gt=0, le=65_535)
    # 日志级别：五个标准级别之一（DEBUG 到 CRITICAL）
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"



# JWT 配置
class JwtSettings(FoundationSettings):
    """JWT 登录令牌相关配置，从环境变量读取"""
    jwt_secret: SecretStr = Field(min_length=32)  # JWT签名密钥，至少32位
    jwt_key_encoding: Literal["raw", "base64"] = "raw"  # 密钥格式：原生字符串 / base64编码
    jwt_issuer: str | None = None  # 签发者标识，可选
    jwt_audience: str | None = None  # 接收方标识，可选
    jwt_leeway_seconds: int = Field(default=0, ge=0, le=300)  # 时间容错偏移，最多5分钟

# Nacos 配置
class NacosSettings(FoundationSettings):
    """Nacos 配置中心连接参数，从环境变量读取"""
    nacos_server_addr: str  # Nacos服务地址，例如 127.0.0.1:8848
    nacos_namespace: str = ""  # Nacos命名空间，默认空（public）
    nacos_username: str | None = None  # Nacos账号，可选
    nacos_password: SecretStr | None = None  # Nacos密码，可选，敏感类型
    nacos_timeout_ms: int = Field(default=3_000, gt=0)  # 请求超时时间，单位毫秒
    nacos_cache_dir: Path  # 本地缓存目录，存放拉取到的配置
    nacos_log_dir: Path  # Nacos客户端日志存放目录

# HTTP 客户端配置
class HttpClientSettings(FoundationSettings):
    """内部HTTP客户端全局参数，用来控制请求、连接池、重试策略"""
    http_connect_timeout_seconds: float = Field(default=3.0, gt=0)  # 建立连接超时
    http_read_timeout_seconds: float = Field(default=10.0, gt=0)  # 读取响应数据超时
    http_write_timeout_seconds: float = Field(default=10.0, gt=0)  # 发送请求体超时
    http_pool_timeout_seconds: float = Field(default=3.0, gt=0)  # 从连接池拿连接的等待超时
    http_max_connections: int = Field(default=100, gt=0)  # 连接池最大总连接数
    http_max_keepalive_connections: int = Field(default=20, ge=0)  # 保活连接上限
    http_retry_attempts: int = Field(default=2, ge=1, le=5)  # 失败重试次数，1~5次
    http_retry_backoff_seconds: float = Field(default=0.1, ge=0, le=5)  # 重试间隔，退避等待时间

# 可观测性配置
class TelemetrySettings(FoundationSettings):
    """OpenTelemetry 链路追踪、指标上报配置"""
    otel_service_name: str  # 服务名称，链路追踪里用来区分服务
    otel_service_version: str = "0.1.0"  # 服务版本号
    otel_environment: str = "local"  # 环境标识：local / dev / prod

# Snowflake 配置
class SnowflakeSettings(FoundationSettings):
    """雪花ID生成器配置，用来生成全局唯一ID"""
    snowflake_worker_id: int = Field(ge=0, le=31)  # 机器编号，范围0~31
    snowflake_data_center_id: int = Field(ge=0, le=31)  # 数据中心编号，范围0~31