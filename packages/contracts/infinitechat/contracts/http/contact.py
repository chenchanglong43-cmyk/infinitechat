"""Contact and friend-application HTTP models."""

from datetime import datetime

from infinitechat.contracts.base import ContractModel

from infinitechat.contracts.enums import FriendApplicationStatus, FriendStatus, SessionType
from pydantic import Field

# 添加好友请求
# 添加好友请求 (数据模型)
class AddFriendRequest(ContractModel):
    msg: str = Field(min_length=1)  # 字段 msg: 字符串, 最短 1

# 处理好友申请请求
# 处理好友申请请求 (数据模型)
class ModifyApplicationRequest(ContractModel):
    # 字段 receiveuser_ids: 字符串列表, 别名 "receiveuserIds "receiveuserIds"
    receiveuser_ids: list[str] = Field(alias="receiveuserIds", min_length=1)

# 申请数量
# 申请数量 (数据模型)
class ApplyCount(ContractModel):
    count: int = Field(ge=0)  # 数量：不能小于 0

# 好友详情
# 好友详情 (数据模型)
class FriendDetail(ContractModel):
    user_id: str  # 用户ID
    nickname: str | None = None  # 昵称：字符串(可为空)，默认 空
    avatar: str | None = None  # 头像：字符串(可为空)，默认 空
    email: str | None = None  # 邮箱：字符串(可为空)，默认 空
    phone: str | None = None  # 手机号：字符串(可为空)，默认 空
    signature: str | None = None  # 签名：字符串(可为空)，默认 空
    gender: int | None = None  # 字段 gender: 整数(可为空), 默认 空
    session_id: str | None = None  # 会话ID：字符串(可为空)，默认 空
    status: FriendStatus  # 状态：好友状态

# 好友摘要
# 好友摘要 (数据模型)
class FriendSummary(ContractModel):
    user_id: str  # 用户ID
    nickname: str | None = None  # 昵称：字符串(可为空)，默认 空
    avatar: str | None = None  # 头像：字符串(可为空)，默认 空
    status: FriendStatus  # 状态：好友状态
    signature: str | None = None  # 签名：字符串(可为空)，默认 空
    session_id: str | None = None  # 会话ID：字符串(可为空)，默认 空

# 好友申请
# 好友申请 (数据模型)
class FriendApplication(ContractModel):
    user_id: str  # 用户ID
    nickname: str | None = None  # 昵称：字符串(可为空)，默认 空
    avatar: str | None = None  # 头像：字符串(可为空)，默认 空
    msg: str  # 字段 msg: 字符串
    status: FriendApplicationStatus  # 状态：好友申请状态
    time: datetime  # 时间
    # 是否接收人：不能小于 0，不能大于 1
    is_receiver: int = Field(ge=0, le=1)

# 新建会话
# 新建会话 (数据模型)
class NewSession(ContractModel):
    user_id: str  # 用户ID
    session_id: str  # 会话ID
    session_type: SessionType  # 会话类型：会话类型
    session_name: str | None = None  # 会话名字：字符串(可为空)，默认 空
    avatar: str | None = None  # 头像：字符串(可为空)，默认 空