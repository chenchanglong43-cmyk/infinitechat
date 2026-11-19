"""Kafka payload and header models."""

from infinitechat.contracts.base import ContractModel, to_camel
from pydantic import ConfigDict, Field

# Kafka事件头部模型，每条kafka消息都带上这一套公共头，用来链路追踪、标记事件信息
class KafkaEventHeaders(ContractModel):
    event_type: str  # 事件类型，比如红包创建、好友申请过期，用来区分消息
    schema_version: str = "1"  # 数据结构版本，以后改字段了就升级版本号，默认1
    event_id: str  # 事件唯一ID，给这条消息单独一个编号
    correlation_id: str  # 关联ID，一串业务链路里所有消息共用同一个，方便追踪整条流程
    traceparent: str | None = None  # 链路追踪标准字段，给OpenTelemetry用，没有就为空
    produced_at: int = Field(ge=0)  # 消息发送时间戳，毫秒，不能是负数

# 好友申请创建事件载荷：发起好友申请时发这条kafka消息
class FriendRequestCreated(ContractModel):
    apply_friend_id: int  # 好友申请记录ID
    create_time: int = Field(ge=0)  # 申请创建的时间戳，不能负数
    expire_time: int = Field(ge=0)  # 这条申请什么时候过期的时间戳

# 好友申请过期事件载荷：好友申请超时自动失效时发的消息
class FriendRequestExpired(ContractModel):
    apply_friend_id: int  # 好友申请记录ID
    expire_time: int = Field(ge=0)  # 过期时间戳

# 红包创建事件载荷：用户发红包的时候推送这条消息
class RedPacketCreated(ContractModel):
    red_packet_id: int  # 红包唯一ID
    create_time: int = Field(ge=0)  # 红包创建时间戳

# 红包领取事件载荷：有人点开领取红包就发这条消息
class RedPacketReceived(ContractModel):
    user_id: int  # 领红包的用户ID
    red_packet_id: int  # 属于哪个红包
    received_amount: int = Field(gt=0, description="Amount in fen")  # 领到的金额，单位分，必须大于0
    receive_time: int = Field(ge=0)  # 领取的时间戳

# 红包完成事件载荷：红包被全部领完，红包流程结束时推送
class RedPacketCompleted(ContractModel):
    red_packet_id: int  # 红包ID

# 红包过期消息序列化函数
# 这个比较特殊，不发JSON，直接只传红包id字符串，省流量
def serialize_red_packet_expiration(red_packet_id: int) -> bytes:
    """The wire value is a plain UTF-8 ID, not a JSON document."""
    if red_packet_id <= 0:
        # 红包ID必须是正数，非法ID直接抛错
        raise ValueError("red_packet_id must be positive")
    # 转成字符串再编码成bytes发给kafka
    return str(red_packet_id).encode()

# 红包过期消息解析函数，把kafka里拿到的二进制内容转回红包id
def parse_red_packet_expiration(payload: bytes | str) -> int:
    # 如果是bytes就解码成文本，已经是字符串就直接用
    value = payload.decode() if isinstance(payload, bytes) else payload
    # 校验：只能是纯数字，并且数字大于0
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise ValueError("red-packet expiration payload must be a positive decimal ID")
    return int(value)

# Canal的flatMessage模式消息模型，用来接收MySQL数据库变更CDC消息
class CanalFlatMessage(ContractModel):
    """Relevant fields emitted by Canal 1.1.7 Kafka MQ flatMessage mode."""

    model_config = ConfigDict(
        alias_generator=to_camel,  # 自动转驼峰命名，适配canal输出的字段名
        extra="allow",  # 允许有额外字段，canal有时候会多出我们没定义的字段，不报错
        populate_by_name=True,  # 驼峰、下划线两种写法都能正常赋值
    )

    id: int | None = None  # canal消息序号
    database: str  # 变更来自哪个数据库
    table: str  # 变更来自哪张表
    pk_names: list[str] | None = None  # 这张表的主键字段名列表
    is_ddl: bool  # 是不是表结构变更（DDL语句，比如alter table）
    type: str  # 变更类型：INSERT / UPDATE / DELETE
    es: int | None = None  # 事件发生时间戳
    ts: int | None = None  # canal推送这条消息的时间戳
    sql: str | None = None  # 原始SQL语句，DDL的时候才有值
    sql_type: dict[str, int] | None = None  # 字段在canal里识别的sql类型
    mysql_type: dict[str, str] | None = None  # 原生mysql字段类型
    data: list[dict[str, str | None]]  # 变更之后的新数据，多条记录放列表
    old: list[dict[str, str | None]] | None = None  # 更新前的旧数据，update的时候才有