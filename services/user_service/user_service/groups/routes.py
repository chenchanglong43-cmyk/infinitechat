"""Group HTTP adapter."""
# 群聊相关HTTP接口适配器

from typing import Annotated
# 类型注解，配合FastAPI的Depends使用

from fastapi import APIRouter, Depends, Request
# 路由、依赖注入、请求对象
from infinitechat.contracts import BaseResponse
# 统一返回包装类
from infinitechat.contracts.http import (
    CreateGroupRequest,
    CreateGroupResponse,
    GroupMemberMutationResponse,
    InviteGroupRequest,
)
# 群模块请求、响应DTO

from user_service.dependencies import current_user_id, get_container, require_same_user
# 依赖工具：拿登录用户ID、获取DI容器、身份校验

router = APIRouter(prefix="/api/group", tags=["groups"])
# 创建路由，前缀/api/group，接口文档分组groups

# 创建群聊
@router.post("")  # 注册POST接口
async def create_group(
    payload: CreateGroupRequest, # 前端传的创建群请求体
    request: Request, # 请求对象，用来拿容器
    authenticated_user_id: Annotated[int, Depends(current_user_id)], # 当前登录用户id
) -> BaseResponse[CreateGroupResponse]:
    require_same_user(authenticated_user_id, payload.creator_id)  # 校验，只能自己建群
    return BaseResponse[CreateGroupResponse].success(
        await get_container(request).groups.create(payload)
        # 调用group服务创建群，返回群信息包成统一响应
    )

# 邀请入群
@router.post("/invite")  # 注册POST接口 /invite
async def invite_group_members(
    payload: InviteGroupRequest, # 邀请入群的请求体
    request: Request, # 请求对象，拿容器
    authenticated_user_id: Annotated[int, Depends(current_user_id)], # 当前登录用户id
) -> BaseResponse[GroupMemberMutationResponse]:
    require_same_user(authenticated_user_id, payload.inviter_id)  # 校验，只能自己发起邀请
    return BaseResponse[GroupMemberMutationResponse].success(
        await get_container(request).groups.invite(payload)
        # 调用group服务执行邀请成员，返回操作结果
    )