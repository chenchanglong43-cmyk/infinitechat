"""Typed system-notification frames."""

from typing import Annotated, Literal

from infinitechat.contracts.base import ContractModel

from infinitechat.contracts.enums import SessionType, SystemNotificationType
from infinitechat.contracts.types import JsonValue
from pydantic import Field, TypeAdapter

# 好友申请通知的消息体，装好友申请相关展示信息
class FriendApplicationNotificationBody(ContractModel):
    nickname: str  # 申请人昵称
    avatar: str | None = None  # 申请人头像链接，没有就为空
    msg: str  # 申请人填写的申请留言

# 新私聊会话通知体，用来展示私聊会话信息
class NewSessionNotificationBody(ContractModel):
    session_name: str  # 会话名称，一般就是对方昵称
    avatar: str | None = None  # 会话头像，没有就为空

# 新群会话通知体，建群成功之后发通知用
class NewGroupSessionNotificationBody(ContractModel):
    session_name: str  # 群名称
    avatar: str | None = None  # 群头像链接，没有就为空
    creator_id: int  # 建群人的用户ID
    members_count: int = Field(ge=1)  # 群成员数量，至少要有1个人

# 系统通知公共基类，所有系统通知都继承这一套通用字段
class _SystemNotificationBase(ContractModel):
    message_id: str  # 这条通知消息的唯一ID
    session_id: int | None = None  # 会话ID，有些通知不属于某个会话，就为空
    sender_id: int | None = None  # 发送方用户ID，系统发出的通知可以为空
    receiver_id: int  # 接收这条通知的用户ID，必填
    session_type: SessionType | None = None  # 会话类型：私聊/群聊，不属于会话就为空
    timestamp: int = Field(ge=0)  # 通知生成时间戳，不能是负数

# 好友申请系统通知
class FriendApplicationNotification(_SystemNotificationBase):
    session_id: None = None  # 好友申请阶段还没有会话，固定为空
    type: Literal[SystemNotificationType.FRIEND_APPLICATION] = (
        SystemNotificationType.FRIEND_APPLICATION
    )
    session_type: None = None  # 不属于私聊/群会话，固定为空
    body: FriendApplicationNotificationBody  # 通知详情，放申请人信息和留言

# 新私聊会话通知
class NewSessionNotification(_SystemNotificationBase):
    session_id: int  # 私聊会话ID，已经创建好了
    sender_id: int  # 发起会话的用户ID
    type: Literal[SystemNotificationType.NEW_SESSION] = SystemNotificationType.NEW_SESSION
    session_type: Literal[SessionType.DIRECT] = SessionType.DIRECT  # 固定是私聊类型
    body: NewSessionNotificationBody  # 私聊会话展示信息

# 新群会话通知
class NewGroupSessionNotification(_SystemNotificationBase):
    session_id: int  # 群会话ID
    type: Literal[SystemNotificationType.NEW_GROUP_SESSION] = (
        SystemNotificationType.NEW_GROUP_SESSION
    )
    session_type: Literal[SessionType.GROUP] = SessionType.GROUP  # 固定是群聊类型
    body: NewGroupSessionNotificationBody  # 群信息详情

# 联合类型：把所有系统通知放一起，用type字段自动区分是哪一种通知
SystemNotification = Annotated[
    FriendApplicationNotification | NewSessionNotification | NewGroupSessionNotification,
    Field(discriminator="type"),  # 靠type这个字段做类型判别，pydantic自动解析成对应模型
]

# 系统通知类型适配器，提前预编译好，解析的时候更快
_SYSTEM_NOTIFICATION_ADAPTER: TypeAdapter[SystemNotification] = TypeAdapter(SystemNotification)

# 解析系统通知json数据，自动校验并且转成上面对应的通知模型
def parse_system_notification(payload: JsonValue) -> SystemNotification:
    """Validate a decoded JSON notification using its stable numeric type."""
    return _SYSTEM_NOTIFICATION_ADAPTER.validate_python(payload)