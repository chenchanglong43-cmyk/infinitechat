"""Gateway runtime settings."""

from typing import Literal

from infinitechat.foundation.settings import FoundationSettings
from pydantic import Field, SecretStr

# 网关配置
# 网关配置 (配置类, 字段从环境变量读)
class GatewaySettings(FoundationSettings):
    app_env: str = "local"  # 应用环境：默认 "local"
    service_name: str = "GateWay"  # 服务名：默认 "GateWay"
    # 监听地址：默认 "0.0.0.0"
    service_host: str = "0.0.0.0"  # noqa: S104 - explicit bind setting
    # 服务对外公布主机：默认 "127.0.0.1"
    service_advertise_host: str = "127.0.0.1"
    # 服务端口：默认 10010，必须大于 0，不能大于 65535
    service_port: int = Field(default=10_010, gt=0, le=65_535)
    # 日志级别：只能是 "DEBUG"/"INFO"/"WARNING"/"ERROR"/ "CRITICAL"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    redis_url: str  # Redis 地址
    redis_db: int = 2  # Redis库号：默认 2
    # 用户令牌密钥：最短 32
    jwt_secret: SecretStr = Field(min_length=32)
    # JWT密钥编码：只能是 "raw"/"base64"，默认 "raw"
    jwt_key_encoding: Literal["raw", "base64"] = "raw"

    nacos_server_addr: str  # Nacos 地址
    nacos_namespace: str = ""  # Nacos命名空间：默认 ""
    nacos_username: str | None = None  # Nacos用户名：字符串(可为空)，默认 空
    # Nacos密码：密钥字符串(可为空)，默认 空
    nacos_password: SecretStr | None = None
    nacos_cache_dir: str  # Nacos 缓存目录
    nacos_log_dir: str  # Nacos 日志目录

    # 上游连接超时秒数：小数
    upstream_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    # 上游已读超时秒数：小数，默认 60.0
    upstream_read_timeout_seconds: float = Field(default=60.0, gt=0)
    # 字段 upstream_write_timeout_seconds: 小数
    upstream_write_timeout_seconds: float = Field(default=10.0, gt=0)
    # 上游池超时秒数：小数，默认 3.0
    upstream_pool_timeout_seconds: float = Field(default=3.0, gt=0)
    # 跨域允许来源：默认
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:10010"
    # 跨域允许凭据：默认 假
    cors_allow_credentials: bool = False