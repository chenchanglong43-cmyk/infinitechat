"""Authentication HTTP adapter."""
# 认证模块的http接口层，把auth服务包装成http接口

from typing import Annotated
# 导入Annotated，用来给参数附加额外元信息，FastAPI常用

from fastapi import APIRouter, Depends, Header, Query, Request
# 导入fastapi需要的路由、依赖注入、请求头、查询参数、请求对象

from infinitechat.contracts import BaseResponse
# 统一返回体基础封装类，所有接口都用这个格式返回
from infinitechat.contracts.http import (
    CodeLoginRequest,
    LoginSessionResponse,
    PasswordLoginRequest,
    TokenResponse,
    UserRegisterRequest,
)
# 导入各个接口的入参、出参契约模型，也就是请求体和返回体结构

from user_service.dependencies import current_user_id, get_container, require_same_user
# 导入依赖工具：拿当前登录用户id、获取DI容器、校验是不是同一个用户

# 创建路由实例，接口前缀 /api/user，分类标签auth
router = APIRouter(prefix="/api/user", tags=["auth"])

# 发送验证码接口
@router.get("/sendCaptcha")
async def send_captcha(
    request: Request,
    # 前端传的目标邮箱，查询参数，别名targetEmail
    target_email: Annotated[str, Query(alias="targetEmail")],
) -> BaseResponse[str]:
    # 从容器拿到auth服务，邮箱统一转小写再发验证码
    result = await get_container(request).auth.send_captcha(target_email.lower())
    # 包装成统一成功响应返回
    return BaseResponse[str].success(result)

# 用户注册接口
@router.post("/register")
async def register(
    # 注册请求体，由pydantic自动校验
    payload: UserRegisterRequest,
    request: Request,
) -> BaseResponse[LoginSessionResponse]:
    # 调用auth注册逻辑，返回登录会话信息
    return BaseResponse[LoginSessionResponse].success(
        await get_container(request).auth.register(payload)
    )

# 密码登录接口
@router.post("/login/password")
async def login_password(
    # 密码登录请求体
    payload: PasswordLoginRequest,
    request: Request,
) -> BaseResponse[LoginSessionResponse]:
    # 调用auth的密码登录方法
    return BaseResponse[LoginSessionResponse].success(
        await get_container(request).auth.login_password(payload)
    )

# 验证码登录接口
@router.post("/login/code")
async def login_code(
    # 验证码登录请求体，里面放邮箱+验证码
    payload: CodeLoginRequest,
    request: Request,
) -> BaseResponse[LoginSessionResponse]:
    # 调用auth验证码登录逻辑
    return BaseResponse[LoginSessionResponse].success(
        await get_container(request).auth.login_code(payload)
    )

# 退出登录接口
@router.get("/logout")
async def logout(
    request: Request,
    # 从token里解析出当前登录用户id，依赖注入自动拿到
    user_id: Annotated[int, Depends(current_user_id)],
) -> BaseResponse[bool]:
    # 调用登出逻辑，销毁会话/令牌
    return BaseResponse[bool].success(await get_container(request).auth.logout(user_id))

# 刷新令牌接口
@router.post("/refresh")
async def refresh(
    request: Request,
    # 从请求头Refresh-Token拿刷新令牌，可选参数
    refresh_token: Annotated[str | None, Header(alias="Refresh-Token")] = None,
) -> BaseResponse[TokenResponse]:
    # 用刷新token换新的access token
    return BaseResponse[TokenResponse].success(
        await get_container(request).auth.refresh(refresh_token)
    )

# 刷新实时地址接口
@router.get("/refresh/uri")
async def refresh_uri(
    request: Request,
    # 前端传入要操作的用户id，query参数userId
    requested_user_id: Annotated[int, Query(alias="userId")],
    # 从token拿到当前登录人id
    authenticated_user_id: Annotated[int, Depends(current_user_id)],
) -> BaseResponse[str]:
    # 校验：登录的人必须和要操作的人是同一个，防止越权
    require_same_user(authenticated_user_id, requested_user_id)
    # 调用服务，刷新该用户的实时长连接地址
    return BaseResponse[str].success(
        await get_container(request).auth.refresh_uri(authenticated_user_id)
    )