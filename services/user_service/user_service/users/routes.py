"""User profile and raw session-query HTTP adapter."""
# 用户资料、会话查询相关HTTP接口层

from typing import Annotated
# 类型注解，给Depends、Query标记用

from fastapi import APIRouter, Depends, Query, Request
# FastAPI路由、依赖注入、查询参数、请求对象
from infinitechat.contracts import BaseResponse
# 统一返回包装类
from infinitechat.contracts.http import (
    UpdateAvatarRequest,
    UploadUrlResponse,
)
# 请求、响应DTO模型

from user_service.dependencies import (
    current_user_id,
    current_user_or_internal,
    get_container,
    require_same_user,
)
# 依赖组件：拿用户ID、内部鉴权、获取DI容器、身份校验

router = APIRouter(prefix="/api/user", tags=["users"])
# 创建路由，接口前缀/api/user，接口文档分组users

# 获取上传地址
@router.get("/uploadUrl")  # 注册GET接口 /uploadUrl
async def upload_url(
    request: Request,  # 请求对象，用来拿容器
    file_name: Annotated[str, Query(alias="fileName")], # 前端传fileName，映射变量
    _user_id: Annotated[int, Depends(current_user_id)], # 拿到当前登录用户id，这里暂未使用
) -> BaseResponse[UploadUrlResponse]:
    return BaseResponse[UploadUrlResponse].success(
        await get_container(request).profiles.upload_urls(file_name)
        # 调用profile服务，生成文件上传地址，包成统一成功返回
    )

# 更新头像
@router.post("/update/avatar")  # 注册POST接口 /update/avatar
async def update_avatar(
    payload: UpdateAvatarRequest, # 前端传的请求体，包含用户id和头像地址
    request: Request, # 请求对象，拿容器
    authenticated_user_id: Annotated[int, Depends(current_user_id)], # 当前登录用户id
) -> BaseResponse[bool]:
    require_same_user(authenticated_user_id, payload.user_id)  # 校验，只能改自己头像
    return BaseResponse[bool].success(
        await get_container(request).profiles.update_avatar(
            authenticated_user_id,
            payload.uri,
        )
        # 调用profile服务更新头像，返回结果包装统一响应
    )

# 会话昵称
@router.get("/get/nickname")  # 注册GET接口 /get/nickname
async def nicknames(
    request: Request, # 请求对象，拿容器
    session_id: Annotated[int, Query(alias="sessionId")], # 前端传会话id参数
    user_id: Annotated[int, Depends(current_user_id)], # 当前登录用户id
) -> dict[int, str]:
    return await get_container(request).profiles.nicknames(user_id, session_id)
    # 查询这个会话对应的昵称，返回id和昵称的字典

# 根据会话 ID 批量换取接收人ID列表
@router.get("/get/receivers")  # 注册GET接口 /get/receivers
async def receivers(
    request: Request, # 请求对象，拿容器
    session_id: Annotated[int, Query(alias="sessionId")], # 前端传会话id
    user_id: Annotated[int | None, Depends(current_user_or_internal)],
    # 两种权限：普通用户或者内部服务调用，内部调用允许为空
) -> list[int]:
    return await get_container(request).profiles.receivers(user_id, session_id)
    # 获取这个会话里面接收人的id列表

# 根据用户 ID 批量换取会话ID列表
@router.get("/get/sessions")  # 注册GET接口 /get/sessions
async def sessions(
    request: Request, # 请求对象，拿容器
    requested_user_id: Annotated[int, Query(alias="userId")], # 前端要查询的目标用户id
    authenticated_user_id: Annotated[int, Depends(current_user_id)], # 当前登录用户id
) -> list[int]:
    require_same_user(authenticated_user_id, requested_user_id)  # 校验，只能查自己会话
    return await get_container(request).profiles.sessions(authenticated_user_id)
    # 返回该用户名下全部会话id列表