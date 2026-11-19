"""Group HTTP models."""

from infinitechat.contracts.base import ContractModel
from infinitechat.contracts.enums import SessionType
from pydantic import Field

# 创建群请求
# 创建群请求 (数据模型)
class CreateGroupRequest(ContractModel):
    creator_id: int  # 字段 creator_id: 整数
    member_ids: list[int]  # 成员ID

# 创建群响应
# 创建群响应 (数据模型)
class CreateGroupResponse(ContractModel):
    creator_id: str  # 字段 creator_id: 字符串
    session_id: str  # 会话ID
    session_name: str  # 会话名字
    # 会话类型：会话类型，默认 会话类型 GROUP
    session_type: SessionType = SessionType.GROUP
    avatar: str | None = None  # 头像：字符串(可为空)，默认 空
    # 失败成员ID：默认值由函数生成 列表
    failed_member_ids: list[str] = Field(default_factory=list)

# 邀请入群请求
# 邀请入群请求 (数据模型)
class InviteGroupRequest(ContractModel):
    session_id: int  # 会话ID
    inviter_id: int  # 字段 inviter_id: 整数
    # 字段 invitee_ids: 整数列表, 最短 1, 最长 100
    invitee_ids: list[int] = Field(min_length=1, max_length=100)

# 群成员变更响应
# 群成员变更响应 (数据模型)
class GroupMemberMutationResponse(ContractModel):
    # 成功ID：默认值由函数生成 列表
    success_ids: list[str] = Field(default_factory=list)
    # 失败ID：默认值由函数生成 列表
    failed_ids: list[str] = Field(default_factory=list)