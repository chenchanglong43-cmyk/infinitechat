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
    # 日志级别：五个标准级别之一（DEBUG 到 CRITICAL）
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"