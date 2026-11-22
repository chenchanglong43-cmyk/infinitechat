"""HTTP request and response contracts grouped by domain."""

from infinitechat.contracts.http.auth import (
    CodeLoginRequest,
    LoginSessionResponse,
    PasswordLoginRequest,
    TokenResponse,
    UpdateAvatarRequest,
    UploadUrlResponse,
    UserRegisterRequest,
)
from infinitechat.contracts.http.contact import (
    AddFriendRequest,
    ApplyCount,
    FriendApplication,
    FriendDetail,
    FriendSummary,
    ModifyApplicationRequest,
    NewSession,
)
from infinitechat.contracts.http.group import (
    CreateGroupRequest,
    CreateGroupResponse,
    GroupMemberMutationResponse,
    InviteGroupRequest,
)
from infinitechat.contracts.http.internal import (
    GroupMemberCount,
    GroupMembership,
    MessageValidation,
    SingleMessageValidateRequest,
    UserInfo,
    UserStatus,
)

__all__ = (
    "AddFriendRequest",
    "ApplyCount",
    "CodeLoginRequest",
    "CreateGroupRequest",
    "CreateGroupResponse",
    "FriendApplication",
    "FriendDetail",
    "FriendSummary",
    "GroupMemberCount",
    "GroupMemberMutationResponse",
    "GroupMembership",
    "InviteGroupRequest",
    "LoginSessionResponse",
    "MessageValidation",
    "ModifyApplicationRequest",
    "NewSession",
    "PasswordLoginRequest",
    "SingleMessageValidateRequest",
    "TokenResponse",
    "UpdateAvatarRequest",
    "UploadUrlResponse",
    "UserInfo",
    "UserRegisterRequest",
    "UserStatus",
)