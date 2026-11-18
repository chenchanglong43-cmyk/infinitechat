"""Stable integer values shared by HTTP, WebSocket, Kafka and persistence boundaries."""
# 全局数字枚举，HTTP、WS、Kafka、数据库都统一用这套值，避免前后端/服务之间数值对不上

from enum import IntEnum
# IntEnum：整型枚举，转成数字直接存数据库、发消息，序列化很方便

# 消息类型
class MessageType(IntEnum):
    TEXT = 0  # 普通文本消息
    IMAGE = 1  # 图片消息
    EMOJI = 2  # 表情消息
    RED_PACKET = 3  # 红包消息

# 会话类型
class SessionType(IntEnum):
    DIRECT = 0  # 私聊会话（一对一）
    GROUP = 1  # 群聊会话

# 系统通知类型
class SystemNotificationType(IntEnum):
    FRIEND_APPLICATION = 101  # 好友申请通知
    NEW_SESSION = 102  # 新增私聊会话
    NEW_GROUP_SESSION = 103  # 被拉进新群
    GROUP_KICK = 104  # 被踢出群聊

# 好友状态
class FriendStatus(IntEnum):
    NON_FRIEND = -1  # 不是好友
    NORMAL = 0  # 正常好友
    BLOCKED = 1  # 被对方拉黑
    DELETED = 2  # 好友已删除

# 好友申请状态
class FriendApplicationStatus(IntEnum):
    UNREAD = 0  # 未读
    ACCEPTED = 1  # 已同意
    REJECTED = 2  # 已拒绝
    READ = 3  # 已读但没处理
    EXPIRED = 4  # 申请过期失效

# 用户账号状态
class UserState(IntEnum):
    NORMAL = 0  # 账号正常
    BANNED = 1  # 账号封禁
    CANCELLED = 2  # 账号已注销

# 用户会话状态（单条会话对当前用户的状态）
class UserSessionStatus(IntEnum):
    NORMAL = 0  # 会话正常可见
    DELETED = 1  # 用户删除该会话（消息还在，只是前端不展示）

# 群成员角色
class GroupMemberRole(IntEnum):
    OWNER = 0  # 群主
    ADMIN = 1  # 管理员
    MEMBER = 2  # 普通成员

# 红包类型
class RedPacketType(IntEnum):
    NORMAL = 0
    RANDOM = 1  # 拼手气随机红包

# 红包整体状态
class RedPacketStatus(IntEnum):
    NOT_EXIST = -1  # 红包记录不存在
    NOT_COMPLETED = 0  # 红包还没领完
    COMPLETED = 1  # 全部领完
    EXPIRED = 2  # 超时没领完，自动过期退款

# 余额流水类型
class BalanceLogType(IntEnum):
    SEND = 0  # 发红包支出
    RECEIVE = 1  # 领取红包收入
    REFUND = 2  # 红包过期退款

# 控制import * 时导出哪些类
__all__ = (
    "BalanceLogType",
    "FriendApplicationStatus",
    "FriendStatus",
    "GroupMemberRole",
    "MessageType",
    "RedPacketStatus",
    "RedPacketType",
    "SessionType",
    "SystemNotificationType",
    "UserSessionStatus",
    "UserState",
)