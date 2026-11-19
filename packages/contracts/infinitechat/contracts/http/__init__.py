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
from infinitechat.contracts.http.internal import (
    GroupMemberCount,
    GroupMembership,
    MessageValidation,
    SingleMessageValidateRequest,
    UserInfo,
    UserStatus,
)

__all__ = (
    "CodeLoginRequest",
    "GroupMemberCount",
    "GroupMembership",
    "LoginSessionResponse",
    "MessageValidation",
    "PasswordLoginRequest",
    "SingleMessageValidateRequest",
    "TokenResponse",
    "UpdateAvatarRequest",
    "UploadUrlResponse",
    "UserInfo",
    "UserRegisterRequest",
    "UserStatus",
)