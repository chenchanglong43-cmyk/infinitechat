"""UserService runtime settings."""
# 用户服务运行时配置

from typing import Literal
# 导入字面量类型，用来限制配置只能选固定几个值

from infinitechat.foundation.settings import FoundationSettings
# 导入基础配置父类，统一配置加载逻辑
from pydantic import Field, SecretStr
# Field用来给配置加校验规则；SecretStr专门存敏感信息，防止日志明文打印

# 用户服务配置类，继承基础配置，自动从环境变量加载参数
class UserServiceSettings(FoundationSettings):
    app_env: str = "local"  # 当前运行环境，本地开发默认local
    service_name: str = "UserService"  # 服务名称，日志、链路追踪里用来标识自己
    service_host: str = "0.0.0.0"  # 监听网卡，0.0.0.0代表允许外部访问 # noqa: S104 - explicit bind setting
    service_advertise_host: str = "127.0.0.1"  # 注册到注册中心对外暴露的地址
    service_port: int = Field(default=8101, gt=0, le=65_535)  # 服务监听端口，限制合法端口范围
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # 日志等级，只能选这几个，默认INFO

    mysql_dsn: str  # MySQL数据库连接串，必填
    redis_url: str  # Redis连接地址，必填
    redis_db: int = 2  # Redis使用几号库，默认2号库
    kafka_bootstrap_servers: str  # Kafka集群地址，必填
    kafka_client_id: str = "infinitechat-user-service"  # Kafka客户端标识

    jwt_secret: SecretStr = Field(min_length=32)  # 用户登录token的密钥，最少32位，敏感值
    jwt_key_encoding: Literal["raw", "base64"] = "raw"  # 密钥格式，原生字符串或者base64编码
    service_jwt_secret: SecretStr = Field(min_length=32)  # 服务间调用token密钥，和用户token分开
    internal_allowed_callers: str = "RealTimeService,OfflineDataService,RedPacketService"
    # 允许调用本服务的内部服务白名单，逗号分隔
    legacy_password_salt: SecretStr | None = None  # 老版本密码加盐值，兼容旧数据，没有就留空
    access_token_ttl_seconds: int = Field(default=30 * 60, gt=0)  # 登录短token有效期，默认30分钟
    refresh_token_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)  # 刷新token有效期，默认7天

    minio_endpoint: str  # MinIO对象存储地址
    minio_access_key: SecretStr  # MinIO账号
    minio_secret_key: SecretStr  # MinIO密钥
    minio_bucket: str = "infinitechat"  # 存放文件的桶名称
    minio_secure: bool = False  # MinIO是否走HTTPS，默认关闭
    presigned_url_ttl_seconds: int = Field(default=300, gt=0, le=3600)  # 文件临时访问链接有效期，最多1小时
    public_gateway_base_url: str = "http://127.0.0.1:10010"  # 对外网关基础地址，用来拼接前端访问地址

    smtp_host: str  # 发邮件的SMTP服务器地址
    smtp_port: int = Field(gt=0, le=65_535)  # SMTP端口
    smtp_username: str  # 发件邮箱账号
    smtp_password: SecretStr  # 邮箱授权码/密码
    smtp_use_tls: bool = True  # 发邮件是否开启TLS加密，默认开
    smtp_sender: str  # 邮件展示的发件人

    snowflake_worker_id: int = Field(ge=0, le=31)  # 雪花算法机器ID，范围0~31
    snowflake_data_center_id: int = Field(ge=0, le=31)  # 雪花算法机房ID，范围0~31
    default_group_avatar_url: str = (
        "https://video.shanyangcode.com/image/default/A9C9C83CCCE043EC8253DB5D7545DCB4-6-2.png"
    )  # 新建群聊默认头像链接

    outbox_batch_size: int = Field(default=100, gt=0, le=500)  # 消息发件箱一次捞多少条事件去发
    outbox_poll_seconds: float = Field(default=0.2, gt=0, le=10)  # 发件箱轮询间隔，0.2秒查一次库
    outbox_max_attempts: int = Field(default=10, gt=0, le=100)  # 消息发送失败最多重试多少次
    scheduler_batch_size: int = Field(default=500, gt=0, le=1000)  # 定时调度器一次处理任务数量
    scheduler_lease_ms: int = Field(default=30_000, gt=1000)  # 调度锁租约时长，30秒，防止多实例重复执行