"""UserService runtime settings."""

from typing import Literal

from infinitechat.foundation.settings import FoundationSettings
from pydantic import Field, SecretStr

# 用户服务配置
# 用户服务配置 (配置类, 字段从环境变量读)
class UserServiceSettings(FoundationSettings):
    app_env: str = "local"  # 应用环境：默认 "local"
    service_name: str = "UserService"  # 服务名：默认 "UserService"
    # 监听地址：默认 "0.0.0.0"
    service_host: str = "0.0.0.0"  # noqa: S104 - explicit bind setting
    # 服务对外公布主机：默认 "127.0.0.1"
    service_advertise_host: str = "127.0.0.1"
    # 服务端口：默认 8101，必须大于 0，不能大于 65535
    service_port: int = Field(default=8101, gt=0, le=65_535)
    # 日志级别：只能是 "DEBUG"/"INFO"/"WARNING"/"ERROR"/ "CRITICAL"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    mysql_dsn: str  # MySQL 连接串
    redis_url: str  # Redis 地址
    redis_db: int = 2  # Redis库号：默认 2

    # 用户令牌密钥：最短 32
    jwt_secret: SecretStr = Field(min_length=32)
    # JWT密钥编码：只能是 "raw"/"base64"，默认 "raw"
    jwt_key_encoding: Literal["raw", "base64"] = "raw"
    # 服务令牌密钥：最短 32
    service_jwt_secret: SecretStr = Field(min_length=32)
    # 内部允许调用方：默认
    internal_allowed_callers: str = "RealTimeService,OfflineDataService,RedPacketService"
    # 字段 legacy_password_salt: 密钥字符串(可为空), 默认 空
    legacy_password_salt: SecretStr | None = None
    # 访问令牌存活秒数：默认 30 * 60
    access_token_ttl_seconds: int = Field(default=30 * 60, gt=0)
    # 刷新令牌存活秒数
    refresh_token_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)

    smtp_host: str  # SMTP主机
    # SMTP端口：必须大于 0，不能大于 65535
    smtp_port: int = Field(gt=0, le=65_535)
    smtp_username: str  # SMTP用户名
    smtp_password: SecretStr  # SMTP密码
    smtp_use_tls: bool = True  # 字段 smtp_use_tls: 布尔值, 默认 真
    smtp_sender: str  # SMTP发送人

    # 雪花机器号：不能小于 0，不能大于 31
    snowflake_worker_id: int = Field(ge=0, le=31)
    # 雪花数据中心号：不能小于 0，不能大于 31
    snowflake_data_center_id: int = Field(ge=0, le=31)