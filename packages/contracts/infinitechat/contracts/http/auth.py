"""Authentication and user HTTP models."""

from typing import Annotated, Self

from infinitechat.contracts.base import ContractModel

from pydantic import EmailStr, Field, StringConstraints, model_validator

Password = Annotated[str, StringConstraints(min_length=6, max_length=128)]  # 密码: 字符串
CaptchaCode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]  # 验证码码: 字符串


# 用户注册请求
# 用户注册请求 (数据模型)
class UserRegisterRequest(ContractModel):
    email: EmailStr  # 邮箱：邮箱str
    password: Password  # 密码：密码
    # 字段 confirm_password: 字符串, 最短 1
    confirm_password: str = Field(min_length=1)
    code: CaptchaCode  # 码：验证码码
    # 昵称：字符串(可为空)，默认 空，最长 50
    nickname: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")  # 整体校验模型
    def passwords_match(self) -> Self:
        if not self.password.strip():
            raise ValueError("密码不能为空")  # 密码不能为空
        if self.password != self.confirm_password:
            raise ValueError("两次密码不一致")  # 两次密码不一致
        return self


# 密码登录请求
# 密码登录请求 (数据模型)
class PasswordLoginRequest(ContractModel):
    email: EmailStr  # 邮箱：邮箱str
    password: Password  # 密码：密码


# 验证码登录请求
# 验证码登录请求 (数据模型)
class CodeLoginRequest(ContractModel):
    email: EmailStr  # 邮箱：邮箱str
    code: CaptchaCode  # 码：验证码码


# 登录会话响应
# 登录会话响应 (数据模型)
class LoginSessionResponse(ContractModel):
    user_id: int  # 用户ID
    email: str  # 邮箱
    nickname: str | None = None  # 昵称：字符串(可为空)，默认 空
    avatar: str | None = None  # 头像：字符串(可为空)，默认 空
    gender: int | None = None  # 字段 gender: 整数(可为空), 默认 空
    description: str | None = None  # 描述：字符串(可为空)，默认 空
    access_token: str  # 访问令牌
    refresh_token: str  # 刷新令牌
    netty_uri: str | None = None  # 字段 netty_uri: 字符串(可为空), 默认 空
    offline_time: int | None = None  # 离线时间：整数(可为空)，默认 空


# Token 响应
# Token 响应 (数据模型)
class TokenResponse(ContractModel):
    access_token: str  # 访问令牌
    refresh_token: str  # 刷新令牌
