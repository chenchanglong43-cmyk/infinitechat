"""Red-packet HTTP models; yuan values remain Decimal internally and JSON numbers externally."""
# 红包相关的 HTTP 数据模型
# 金额策略：内部一律用 Decimal 计算，避免浮点误差；转 JSON 给前端时再转成数字（float）

from decimal import Decimal  # 十进制类型：金额精确计算用

from infinitechat.contracts.base import ContractModel  # 所有契约模型的基类
from infinitechat.contracts.enums import (  # 相关枚举
    MessageType,  # 消息类型（文本/红包/图片等）
    RedPacketStatus,  # 红包状态（待领/部分领取/领完/过期）
    RedPacketType,  # 红包类型（普通/拼手气）
    SessionType,  # 会话类型（私聊/群聊）
)
from pydantic import Field, field_serializer  # 字段约束；自定义 JSON 序列化

Yuan = Decimal  # 语义别名：看到 Yuan 就表示“单位是元的金额”

# 红包内容
class RedPacketBody(ContractModel):
    red_packet_type: RedPacketType  # 红包类型：普通红包 / 拼手气红包
    # 总金额：元，必须大于 0，小数位最多 2
    total_amount: Yuan = Field(gt=0, decimal_places=2)
    total_count: int = Field(gt=0)  # 总份数：必须大于 0
    # 红包文案：字符串(可为空)，默认 空
    red_packet_wrapper_text: str | None = None

    # 下面的函数负责把 "total_amount" 转成 JSON 里的数字
    # 注意：只影响 JSON 序列化（model_dump_json），内部 model_dump() 仍然是 Decimal
    @field_serializer("total_amount", when_used="json")
    def serialize_total_amount(self, amount: Decimal) -> float:
        return float(amount)

# 发送红包请求
class RedPacketSendRequest(ContractModel):
    session_id: int  # 会话ID：红包发到哪个聊天里
    receiver_id: int | None = None  # 接收人ID：私聊一般要填，群聊可以不填
    sender_id: int  # 发送人ID
    # 类型：消息类型，默认 消息类型 RED_PACKET
    type: MessageType = MessageType.RED_PACKET
    session_type: SessionType  # 会话类型：私聊 / 群聊
    body: RedPacketBody  # 消息体：红包的具体内容
    # 客户端消息ID：字符串(可为空)，默认 空
    client_message_id: str | None = None

# 领取红包请求
class RedPacketReceiveRequest(ContractModel):
    user_id: int  # 用户ID：谁要领
    red_packet_id: int  # 红包ID：领哪个红包

# 发送红包响应
class RedPacketSendResponse(ContractModel):
    red_packet_id: int  # 红包ID
    message_id: int  # 消息ID：红包同时是一条聊天消息

# 领取结果
class ReceiveResult(ContractModel):
    status: RedPacketStatus  # 状态：领取结果对应的红包状态
    message: str  # 消息：给用户看的提示文案
    # 金额：元(可为空)，默认 空，不能小于 0，小数位最多 2
    amount: Yuan | None = Field(default=None, ge=0, decimal_places=2)

    @field_serializer("amount", when_used="json")  # 下面的函数负责把 "amount" 转成 JSON 里的数字
    def serialize_amount(self, amount: Decimal | None) -> float | None:
        # 没领到钱时 amount 是 None，直接返回 None；否则转成数字给前端
        return None if amount is None else float(amount)

# 红包基本信息
class RedPacketBasic(ContractModel):
    red_packet_id: int  # 红包ID
    red_packet_type: RedPacketType  # 红包类型：普通 / 拼手气
    # 总金额：元，不能小于 0，小数位最多 2
    total_amount: Yuan = Field(ge=0, decimal_places=2)
    total_count: int = Field(ge=0)  # 总份数：不能小于 0
    received_count: int = Field(ge=0)  # 已领份数：不能小于 0
    # 已领金额：元，不能小于 0，小数位最多 2
    received_amount: Yuan = Field(ge=0, decimal_places=2)
    status: RedPacketStatus  # 状态：红包状态
    created_time: str  # 创建时间：已格式化的字符串

    # 下面的函数负责把 "total_amount", "received_amount" 转成 JSON 里的数字
    @field_serializer("total_amount", "received_amount", when_used="json")
    def serialize_money(self, amount: Decimal) -> float:
        return float(amount)

# 红包领取记录
class RedPacketReceiveRecord(ContractModel):
    receiver_id: int  # 接收人ID：谁领的
    # 接收人昵称：字符串(可为空)，默认 空
    receiver_nickname: str | None = None
    receiver_avatar: str | None = None  # 接收人头像：字符串(可为空)，默认 空
    amount: Yuan = Field(ge=0, decimal_places=2)  # 金额：元，不能小于 0，小数位最多 2
    received_at: str  # 领取时间：已格式化的字符串

    @field_serializer("amount", when_used="json")  # 下面的函数负责把 "amount" 转成 JSON 里的数字
    def serialize_amount(self, amount: Decimal) -> float:
        return float(amount)

# 红包详情
class RedPacketDetail(RedPacketBasic):
    sender_id: int  # 发送人ID
    sender_nickname: str | None = None  # 发送人昵称：字符串(可为空)，默认 空
    sender_avatar: str | None = None  # 发送人头像：字符串(可为空)，默认 空
    session_id: int  # 会话ID：红包属于哪个聊天
    session_type: SessionType  # 会话类型：私聊 / 群聊
    # 红包文案：字符串(可为空)，默认 空
    red_packet_wrapper_text: str | None = None
    # 领取记录：红包领取记录列表
    receive_records: list[RedPacketReceiveRecord]
