"""UserService runtime settings."""
# 用户服务运行时配置

from typing import Literal  # 导入字面量类型，限制枚举值

from infinitechat.foundation.settings import FoundationSettings  # 导入基础配置父类
from pydantic import Field, SecretStr  # 导入字段校验工具、保密字符串类型

# 用户服务配置
# 用户服务配置 (配置类, 字段从环境变量读)
class UserServiceSettings(FoundationSettings):
    app_env: str = "local"  # 应用环境：默认 "local"
    service_name: str = "UserService"  # 服务名：默认 "UserService"
    service_host: str = "0.0.0.0"  # noqa: S104 - 监听地址：默认 "0.0.0.0"，显式绑定配置
    service_advertise_host: str = "127.0.0.1"  # 服务对外公布主机：默认 "127.0.0.1"
    service_port: int = Field(default=8101, gt=0, le=65_535)  # 服务端口：默认8101，合法端口范围校验
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"  # 日志级别，限定可选值，默认INFO

    mysql_dsn: str  # MySQL 连接串
    redis_url: str  # Redis 地址
    redis_db: int = 2  # Redis库号：默认 2
    kafka_bootstrap_servers: str  # Kafka 集群地址
    kafka_client_id: str = "infinitechat-user-service"  # Kafka客户端ID：默认 "infinitechat-user-service"

    jwt_secret: SecretStr = Field(min_length=32)  # 用户令牌密钥：最短32位
    jwt_key_encoding: Literal["raw", "base64"] = "raw"  # JWT密钥编码：仅raw/base64，默认raw
    service_jwt_secret: SecretStr = Field(min_length=32)  # 服务间调用令牌密钥：最短32位
    internal_allowed_callers: str = "RealTimeService,OfflineDataService,RedPacketService"  # 内部允许调用方列表
    legacy_password_salt: SecretStr | None = None  # 旧版密码加盐值，可为空，默认空
    access_token_ttl_seconds: int = Field(default=30 * 60, gt=0)  # 访问令牌存活秒数：默认30分钟
    refresh_token_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)  # 刷新令牌存活秒数：默认7天

    nacos_server_addr: str  # Nacos 服务地址
    nacos_namespace: str = ""  # Nacos命名空间：默认空字符串
    nacos_username: str | None = None  # Nacos用户名：可为空，默认空
    nacos_password: SecretStr | None = None  # Nacos密码：保密类型，可为空，默认空
    nacos_cache_dir: str  # Nacos 缓存目录
    nacos_log_dir: str  # Nacos 日志目录

    minio_endpoint: str  # MinIO对象存储端点地址
    minio_access_key: SecretStr  # MinIO访问密钥
    minio_secret_key: SecretStr  # MinIO私密密钥
    minio_bucket: str = "infinitechat"  # MinIO存储桶名称：默认infinitechat
    minio_secure: bool = False  # MinIO是否启用HTTPS：默认关闭
    presigned_url_ttl_seconds: int = Field(default=300, gt=0, le=3600)  # 预签名地址存活秒数：默认300秒，上限1小时
    public_gateway_base_url: str = "http://127.0.0.1:10010"  # 公开网关基础地址

    smtp_host: str  # SMTP邮件服务器主机
    smtp_port: int = Field(gt=0, le=65_535)  # SMTP端口，合法端口范围校验
    smtp_username: str  # SMTP登录用户名
    smtp_password: SecretStr  # SMTP登录密码
    smtp_use_tls: bool = True  # 是否开启TLS加密：默认开启
    smtp_sender: str  # 邮件发送人邮箱地址

    snowflake_worker_id: int = Field(ge=0, le=31)  # 雪花算法机器号：取值0~31
    snowflake_data_center_id: int = Field(ge=0, le=31)  # 雪花算法数据中心号：取值0~31
    default_group_avatar_url: str = (
        "https://video.shanyangcode.com/image/default/A9C9C83CCCE043EC8253DB5D7545DCB4-6-2.png"
    )  # 默认群头像地址

    outbox_batch_size: int = Field(default=100, gt=0, le=500)  # 待发事件每批数量：默认100，上限500
    outbox_poll_seconds: float = Field(default=0.2, gt=0, le=10)  # 待发事件轮询间隔秒数：默认0.2秒
    outbox_max_attempts: int = Field(default=10, gt=0, le=100)  # 待发事件最大重试次数：默认10次
    scheduler_batch_size: int = Field(default=500, gt=0, le=1000)  # 调度器每批处理数量：默认500，上限1000
    scheduler_lease_ms: int = Field(default=30_000, gt=1000)  # 调度器租约毫秒数：默认30000毫秒，最小1000