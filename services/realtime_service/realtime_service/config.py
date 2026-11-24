"""RealtimeService runtime settings."""

# 限制字符串只能是指定几个值
from typing import Literal

# 基础配置父类，自动从环境变量加载配置
from infinitechat.foundation.settings import FoundationSettings
# 字段校验工具 + 保密字符串类型，不会明文打印密钥
from pydantic import Field, SecretStr


# 实时服务配置类，所有参数优先从环境变量读取
class RealtimeSettings(FoundationSettings):
    service_name: str = "RealTimeService"  # 服务名称，日志和注册中心会用到
    service_host: str = "0.0.0.0"  # 本机监听地址，0.0.0.0代表监听所有网卡 # noqa: S104
    service_advertise_host: str = "127.0.0.1"  # 对外广播地址，给别的服务连接用
    websocket_port: int = Field(default=9101, gt=0, le=65_535)  # ws端口，限制合法端口范围
    websocket_path: str = "/ws/netty"  # ws连接路径，客户端要拼这个路径连上
    websocket_public_url: str | None = None  # 公网ws地址，有反向代理的时候填
    instance_id: str  # 当前服务实例唯一id，多实例部署用来区分
    redis_url: str  # redis连接地址，存连接状态、会话信息
    redis_db: int = 2  # redis使用的库编号，默认用2号库
    kafka_bootstrap_servers: str  # kafka集群地址，用来收发消息事件
    kafka_client_id: str = "infinitechat-realtime"  # kafka客户端标识，日志监控区分
    jwt_secret: SecretStr = Field(min_length=32)  # 用户jwt签名密钥，长度至少32位，保密类型
    jwt_key_encoding: Literal["raw", "base64"] = "raw"  # jwt密钥格式，原生或者base64编码
    service_jwt_secret: SecretStr = Field(min_length=32)  # 服务间通信的jwt密钥，内部调用鉴权
    nacos_server_addr: str  # nacos服务地址，用来做服务发现、配置拉取
    nacos_namespace: str = ""  # nacos命名空间，用来隔离环境，默认空
    nacos_username: str | None = None  # nacos登录账号，没有就不填
    nacos_password: SecretStr | None = None  # nacos登录密码，保密类型，不会打印明文
    nacos_cache_dir: str  # nacos本地缓存目录，缓存拉到的配置
    nacos_log_dir: str  # nacos的日志存放目录
    snowflake_worker_id: int = Field(ge=0, le=31)  # 雪花算法机器id，范围0~31
    snowflake_data_center_id: int = Field(ge=0, le=31)  # 雪花算法数据中心id，范围0~31
    max_frame_bytes: int = 65_536  # ws单条消息最大字节，防止超大包攻击
    send_timeout_seconds: float = 5.0  # 推送消息超时时间，发不出去就放弃
    connection_ttl_seconds: int = 45  # 连接最大存活时长，超时自动踢下线
    heartbeat_seconds: int = 15  # 心跳间隔，客户端每隔这么久发心跳保活