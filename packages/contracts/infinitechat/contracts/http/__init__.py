"""HTTP request and response contracts grouped by domain."""
# HTTP契约聚合导出文件
# 统一收拢内部接口的DTO，别的模块导入时写起来更简洁，不用一层层找路径

from infinitechat.contracts.http.internal import (
    GroupMemberCount,
    GroupMembership,
    MessageValidation,
    SingleMessageValidateRequest,
    UserInfo,
    UserStatus,
)

# 控制 from xxx import * 能导出哪些模型
__all__ = (
    "GroupMemberCount",
    "GroupMembership",
    "MessageValidation",
    "SingleMessageValidateRequest",
    "UserInfo",
    "UserStatus",
)