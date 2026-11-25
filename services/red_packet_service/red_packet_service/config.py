"""RedPacketService runtime settings."""
# 红包服务的运行配置：继承 FoundationSettings，字段默认从环境变量读取

from typing import Literal

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from infinitechat.foundation.settings import FoundationSettings  # 基础配置：负责从环境变量加载
from pydantic import Field, SecretStr  # 字段约束；敏感值用 SecretStr 包一层，打印时自动打码

# 将 Redis URL 切换到指定 db
def redis_url_for_database(url: str, database: int) -> str:
    # 拆开 URL，拿到 scheme、主机、路径、查询参数
    parsed = urlsplit(url)
    # 把查询参数里的 db 去掉，避免后面又拼一个 db 参数造成冲突
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query) if key.lower() != "db"]
    )
    # 重新拼 URL：路径部分换成 /{database}
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", query, parsed.fragment))

# 红包服务配置
class RedPacketSettings(FoundationSettings):
    # 服务名：注册到 Nacos、日志里都用它
    service_name: str = "RedPacketService"
    # 监听地址：0.0.0.0 表示所有网卡都能连（容器里常用）
    service_host: str = "0.0.0.0"  # noqa: S104
    # 对外公布的主机：别的服务通过这个地址访问我
    service_advertise_host: str = "127.0.0.1"
    # 服务端口：默认 8104
    service_port: int = 8104
    # MySQL 连接串：红包数据落库用
    mysql_dsn: str
    # Redis 地址：红包业务数据（库存、领取记录缓存等）
    redis_url: str
    # 红包业务用哪个 Redis 库，默认 0
    redis_db: int = 0
    # 鉴权用哪个 Redis 库，默认 2（和用户服务共享 token 校验）
    auth_redis_db: int = 2
    # Kafka 集群地址：发红包创建/领取/完成/过期事件用
    kafka_bootstrap_servers: str
    # Kafka 客户端标识：日志、监控里区分是红包服务
    kafka_client_id: str = "infinitechat-red-packet"
    # 用户令牌密钥：校验用户 Access-Token，至少 32 位
    jwt_secret: SecretStr = Field(min_length=32)
    # JWT 密钥编码：原生字符串还是 base64，默认 raw
    jwt_key_encoding: Literal["raw", "base64"] = "raw"
    # 服务令牌密钥：服务之间互相调用时签发/验签，至少 32 位
    service_jwt_secret: SecretStr = Field(min_length=32)
    # Nacos 地址：注册自己、发现其他服务
    nacos_server_addr: str
    # Nacos 命名空间：默认空，表示 public
    nacos_namespace: str = ""
    # Nacos 用户名：没开鉴权就为空
    nacos_username: str | None = None
    # Nacos 密码：没开鉴权就为空，SecretStr 防止日志泄露
    nacos_password: SecretStr | None = None
    # Nacos 本地缓存目录
    nacos_cache_dir: str
    # Nacos 日志目录
    nacos_log_dir: str
    # 雪花机器号：0~31，多实例不能重复
    snowflake_worker_id: int = Field(ge=0, le=31)
    # 雪花数据中心号：0~31，多机房区分
    snowflake_data_center_id: int = Field(ge=0, le=31)
    # 红包过期时间：默认 24 小时，最少 60 秒
    red_packet_expire_seconds: int = Field(default=24 * 3600, ge=60)
    # 红包缓存 TTL：默认 25 小时，比过期时间多 1 小时，留缓冲
    red_packet_cache_ttl_seconds: int = Field(default=25 * 3600, ge=3600)
    # 对账任务每批处理多少条红包
    red_packet_reconcile_batch_size: int = Field(default=500, ge=1, le=5000)
    # Outbox 每批取出多少条待发事件，默认 100
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    # Outbox 空轮询间隔：默认 0.2 秒，越小延迟越低、查库越频繁
    outbox_poll_seconds: float = Field(default=0.2, gt=0)
    # 单条事件最多重试几次，默认 5
    outbox_max_attempts: int = Field(default=5, ge=1, le=20)
    # 调度器每批认领多少条到期任务，默认 500
    scheduler_batch_size: int = Field(default=500, ge=1, le=5000)
    # 调度器认领租约：默认 30 秒，处理超过这个时间会被别人回收
    scheduler_lease_ms: int = Field(default=30_000, ge=1000)
    # 调度器轮询间隔：默认 1 秒
    scheduler_poll_seconds: float = Field(default=1.0, gt=0)
