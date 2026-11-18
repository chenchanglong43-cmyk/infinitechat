"""Service-authenticated UserService validation models."""
# UserService 对外契约模型，给其他服务调用、做权限和消息校验用
from infinitechat.contracts.base import ContractModel
# ContractModel：项目统一的契约基础模型，一般继承自Pydantic BaseModel

from infinitechat.contracts.enums import GroupMemberRole, UserState
# 导入之前写好的整型枚举：群角色、用户账号状态
from infinitechat.contracts.errors import ErrorCode
# 全局错误码枚举，校验不通过时返回

# 用户状态返回模型
class UserStatus(ContractModel):
    user_id: int
    # 用户ID
    state: UserState | None = None
    # 账号状态枚举，可选
    state_name: str
    # 状态文字描述，方便前端展示

# 群成员数量模型
class GroupMemberCount(ContractModel):
    session_id: int
    # 会话ID，群聊对应的session_id
    member_count: int
    # 当前群里面的总人数

# 群成员关系模型，用来判断用户是不是群成员、是什么角色
class GroupMembership(ContractModel):
    user_id: int
    # 用户ID
    session_id: int
    # 群会话ID
    is_member: bool
    # 是否属于这个群的成员
    role: GroupMemberRole | None = None
    # 群角色：群主/管理员/普通成员，不是成员时为空

# 单聊消息权限校验入参
class SingleMessageValidateRequest(ContractModel):
    sender_id: int
    # 发送方用户ID
    receiver_id: int
    # 接收方用户ID
    session_id: int
    # 私聊会话ID

# 消息校验结果返回模型，给网关/消息服务判断能不能发消息
class MessageValidation(ContractModel):
    allowed: bool
    # 是否允许发送这条消息
    reject_reason: str | None = None
    # 拒绝原因，allowed=false时填
    error_code: ErrorCode | None = None
    # 业务错误码，供上游做分支判断

# 基础用户信息模型，跨服务拉取用户资料用
class UserInfo(ContractModel):
    user_id: int
    # 用户唯一ID
    nickname: str | None = None
    # 昵称，允许为空
    avatar: str | None = None
    # 头像链接，允许为空