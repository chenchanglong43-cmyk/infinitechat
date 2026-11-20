"""UserService runtime settings."""

# 导入类型字面量，用来限制选项
from typing import Literal

# 导入基础配置父类
from infinitechat.foundation.settings import FoundationSettings
# 导入pydantic字段校验和保密字符串类型
from pydantic import Field, SecretStr

# 用户服务配置类，所有字段自动从环境变量读取
class UserServiceSettings(FoundationSettings):
    app_env: str = "local"  # 当前运行环境，默认本地开发环境
    service_name: str = "UserService"  # 服务名称，用于日志、链路追踪
    service_host: str = "0.0.0.0"  # 监听地址，0.0.0.0代表允许外部访问 # noqa: S104 - explicit bind setting
    service_advertise_host: str = "127.0.0.1"  # 对外广播的地址，给其他服务发现用
    service_port: int = Field(default=8101, gt=0, le=65_535)  # 服务端口，范围1~65535，默认8101
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"  # 日志级别，只能选这几个值，默认INFO

    mysql_dsn: str  # MySQL连接串，必填，用来连数据库
    redis_url: str  # Redis连接地址，必填
    redis_db: int = 2  # Redis使用的库号，默认2

    jwt_secret: SecretStr = Field(min_length=32)  # 用户JWT签名密钥，最少32位，SecretStr不会明文打印日志
    jwt_key_encoding: Literal["raw", "base64"] = "raw"  # JWT密钥编码格式，raw原始字符串 / base64编码串
    service_jwt_secret: SecretStr = Field(min_length=32)  # 服务间调用的内部令牌密钥，最少32位
    internal_allowed_callers: str = "RealTimeService,OfflineDataService,RedPacketService"  # 允许调用本服务的内部服务列表，逗号分隔
    legacy_password_salt: SecretStr | None = None  # 旧版密码加盐值，兼容老数据，可空
    access_token_ttl_seconds: int = Field(default=30 * 60, gt=0)  # access_token有效期，默认30分钟
    refresh_token_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)  # refresh_token有效期，默认7天

    minio_endpoint: str  # MinIO对象存储地址，必填
    minio_access_key: SecretStr  # MinIO访问密钥ID
    minio_secret_key: SecretStr  # MinIO密钥
    minio_bucket: str = "infinitechat"  # 默认存储桶名称
    minio_secure: bool = False  # MinIO是否开启HTTPS，默认关闭
    presigned_url_ttl_seconds: int = Field(default=300, gt=0, le=3600)  # 预签名链接有效期，默认5分钟，最长1小时
    public_gateway_base_url: str = "http://127.0.0.1:10010"  # 网关基础地址，前端用这个访问资源

    smtp_host: str  # SMTP邮件服务器地址，发验证码邮件用
    smtp_port: int = Field(gt=0, le=65_535)  # SMTP端口，1~65535
    smtp_username: str  # SMTP登录账号
    smtp_password: SecretStr  # SMTP登录密码
    smtp_use_tls: bool = True  # 是否开启TLS加密，默认开启
    smtp_sender: str  # 发件人邮箱地址

    snowflake_worker_id: int = Field(ge=0, le=31)  # 雪花算法机器ID，范围0~31
    snowflake_data_center_id: int = Field(ge=0, le=31)  # 雪花算法数据中心ID，范围0~31
    default_group_avatar_url: str = (  # 群聊默认头像链接
        "https://video.shanyangcode.com/image/default/A9C9C83CCCE043EC8253DB5D7545DCB4-6-2.png"
    )