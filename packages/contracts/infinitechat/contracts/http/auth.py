"""Authentication and user HTTP models."""

from typing import Annotated, Self

from infinitechat.contracts.base import ContractModel

from pydantic import EmailStr, Field, StringConstraints, model_validator

# 密码类型，限制长度6~128位
Password = Annotated[str, StringConstraints(min_length=6, max_length=128)]
# 验证码类型，固定6位数字
CaptchaCode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]

# 用户注册请求体
class UserRegisterRequest(ContractModel):
    email: EmailStr  # 注册邮箱，pydantic自动校验邮箱格式
    password: Password  # 登录密码
    confirm_password: str = Field(min_length=1)  # 二次确认密码
    code: CaptchaCode  # 6位邮箱验证码
    nickname: str | None = Field(default=None, max_length=50)  # 昵称，选填，最多50个字

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        # 校验密码不能全是空格
        if not self.password.strip():
            raise ValueError("密码不能为空")
        # 校验两次输入的密码必须一模一样
        if self.password != self.confirm_password:
            raise ValueError("两次密码不一致")
        return self

# 密码登录请求体
class PasswordLoginRequest(ContractModel):
    email: EmailStr  # 登录邮箱
    password: Password  # 登录密码

# 验证码登录请求体
class CodeLoginRequest(ContractModel):
    email: EmailStr  # 登录邮箱
    code: CaptchaCode  # 6位验证码

# 更新头像请求体
class UpdateAvatarRequest(ContractModel):
    uri: str | None = None  # 头像文件的地址，不传就清空头像
    user_id: int  # 要改头像的用户id

# 登录成功返回给前端的数据
class LoginSessionResponse(ContractModel):
    user_id: int  # 用户唯一id
    email: str  # 用户邮箱
    nickname: str | None = None  # 昵称，没设置就是空
    avatar: str | None = None  # 头像链接
    gender: int | None = None  # 性别，用数字标识
    description: str | None = None  # 个人简介
    access_token: str  # 短时效访问令牌，用来调业务接口
    refresh_token: str  # 长时效刷新令牌，用来换新access_token
    netty_uri: str | None = None  # 实时消息websocket地址，连不上就为空
    offline_time: int | None = None  # 上次离线时间戳，前端用来拉离线消息

# 刷新token接口的返回体
class TokenResponse(ContractModel):
    access_token: str  # 新的访问令牌
    refresh_token: str  # 新的刷新令牌

# 获取文件上传地址返回体
class UploadUrlResponse(ContractModel):
    upload_url: str  # 前端往这个地址上传文件
    download_url: str  # 上传完成后，用这个地址访问文件