"""UserService runtime settings."""

from typing import Literal

from infinitechat.foundation.settings import FoundationSettings
from pydantic import Field, SecretStr

# 用户服务配置类，所有字段自动从环境变量加载
class UserServiceSettings(FoundationSettings):
    app_env: str = "local"  # 运行环境，local/dev/prod
    service_name: str = "UserService"  # 服务名称，日志、监控里用来标识
    service_host: str = "0.0.0.0"  # 监听网卡，0.0.0.0代表允许外部访问
    service_advertise_host: str = "127.0.0.1"  # 别的服务用来连我的地址
    service_port: int = Field(default=8101, gt=0, le=65_535)  # 服务端口，默认8101
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"  # 日志打印等级

    mysql_dsn: str  # MySQL连接地址，账号密码都写在这串里
    redis_url: str  # Redis连接地址
    redis_db: int = 2  # 用Redis的第2个库，分开业务数据

    jwt_secret: SecretStr = Field(min_length=32)  # 用户登录JWT签名密钥，至少32位，保密
    jwt_key_encoding: Literal["raw", "base64"] = "raw"  # 密钥是原生字符串还是base64编码
    service_jwt_secret: SecretStr = Field(min_length=32)  # 微服务之间内部调用的密钥
    internal_allowed_callers: str = "RealTimeService,OfflineDataService,RedPacketService"  # 允许哪些内部服务访问
    legacy_password_salt: SecretStr | None = None  # 老MD5密码的盐，用来兼容旧账号，没有就空
    access_token_ttl_seconds: int = Field(default=30 * 60, gt=0)  # access_token有效期，默认30分钟
    refresh_token_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)  # refresh_token有效期，默认7天

    minio_endpoint: str  # MinIO对象存储地址，存头像、文件
    minio_access_key: SecretStr  # MinIO账号
    minio_secret_key: SecretStr  # MinIO密码
    minio_bucket: str = "infinitechat"  # 存储桶名称，文件都放这个桶里
    minio_secure: bool = False  # MinIO是否用HTTPS，本地开发一般关
    presigned_url_ttl_seconds: int = Field(default=300, gt=0, le=3600)  # 上传下载临时链接有效期，最多1小时
    public_gateway_base_url: str = "http://127.0.0.1:10010"  # 网关地址，前端走网关访问

    smtp_host: str  # 发邮件的SMTP服务器地址
    smtp_port: int = Field(gt=0, le=65_535)  # SMTP端口
    smtp_username: str  # 发件邮箱账号
    smtp_password: SecretStr  # 邮箱授权码/密码
    smtp_use_tls: bool = True  # 是否开启TLS加密传输邮件
    smtp_sender: str  # 对外显示的发件人邮箱

    snowflake_worker_id: int = Field(ge=0, le=31)  # 雪花算法机器编号，0~31
    snowflake_data_center_id: int = Field(ge=0, le=31)  # 雪花算法数据中心编号，0~31