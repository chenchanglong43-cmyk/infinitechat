"""Contact and friend-request HTTP adapter."""
# 联系人、好友申请相关HTTP接口适配器

from typing import Annotated
# Annotated，用来给FastAPI做类型注解

from fastapi import APIRouter, Depends, Path, Query, Request
# 路由、依赖注入、路径参数、查询参数、请求对象

from infinitechat.contracts import BaseResponse, PageRequest, PageResponse
# 统一返回体、分页请求、分页返回结构体
from infinitechat.contracts.http import (
    AddFriendRequest,
    ApplyCount,
    FriendApplication,
    FriendDetail,
    FriendSummary,
    ModifyApplicationRequest,
    NewSession,
)
# 好友模块所有请求、响应DTO

from user_service.dependencies import current_user_id, get_container, require_same_user
# 依赖工具：拿当前用户ID、获取DI容器、身份校验

router = APIRouter(prefix="/api/contact", tags=["contacts"])
# 创建路由，前缀/api/contact，接口文档分组contacts

# 绑定路径用户
async def bound_user_id(
    user_id: Annotated[int, Path(alias="userId")],
    authenticated_user_id: Annotated[int, Depends(current_user_id)],
) -> int:
    # 校验url路径里的userId和登录用户是同一个人
    require_same_user(authenticated_user_id, user_id)
    # 校验通过，返回登录用户id
    return authenticated_user_id

# 搜索用户
@router.get("/{userId}/user/search")
async def search_user(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    keyword: Annotated[str, Query()],
) -> BaseResponse[FriendDetail]:
    # 调用联系人服务，按关键词搜索用户
    return BaseResponse[FriendDetail].success(
        await get_container(request).contacts.search(user_id, keyword)
    )

# 好友列表
@router.get("/{userId}/friend")
async def list_friends(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    page_num: Annotated[int, Query(alias="pageNum", ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    key: str = "",
) -> BaseResponse[PageResponse[FriendSummary]]:
    # 组装分页参数，查好友列表，支持模糊搜索
    result = await get_container(request).contacts.list_friends(
        user_id,
        PageRequest(page_num=page_num, page_size=page_size),
        key=key,
    )
    return BaseResponse[PageResponse[FriendSummary]].success(result)

# 发送好友申请
@router.post("/{userId}/friend/{receiveuserId}")
async def send_friend_request(
    payload: AddFriendRequest,
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    receiver_id: Annotated[int, Path(alias="receiveuserId")],
) -> BaseResponse[bool]:
    # 发起好友申请，带上申请留言
    return BaseResponse[bool].success(
        await get_container(request).contacts.send_friend_request(
            user_id,
            receiver_id,
            payload.msg,
        )
    )

# 申请列表
@router.get("/{userId}/apply")
async def applications(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    page_num: Annotated[int, Query(alias="pageNum", ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
) -> BaseResponse[PageResponse[FriendApplication]]:
    # 分页查询收到的好友申请
    result = await get_container(request).contacts.applications(
        user_id,
        PageRequest(page_num=page_num, page_size=page_size),
    )
    return BaseResponse[PageResponse[FriendApplication]].success(result)

# 未读申请数
@router.get("/{userId}/applyCount")
async def unread_count(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
) -> BaseResponse[ApplyCount]:
    # 获取未读好友申请数量
    count = await get_container(request).contacts.unread_count(user_id)
    return BaseResponse[ApplyCount].success(ApplyCount(count=count))

# 删除好友
@router.delete("/{userId}/friend/{receiveuserId}")
async def delete_friend(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    friend_id: Annotated[int, Path(alias="receiveuserId")],
) -> BaseResponse[bool]:
    # 删除指定好友关系
    return BaseResponse[bool].success(
        await get_container(request).contacts.delete_friend(user_id, friend_id)
    )

# 拉黑好友
@router.post("/{userId}/block/{receiveuserId}")
async def block_friend(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    friend_id: Annotated[int, Path(alias="receiveuserId")],
) -> BaseResponse[bool]:
    # 拉黑用户
    return BaseResponse[bool].success(
        await get_container(request).contacts.block_friend(user_id, friend_id)
    )

# 取消拉黑
@router.delete("/{userId}/block/{receiveuserId}")
async def unblock_friend(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    friend_id: Annotated[int, Path(alias="receiveuserId")],
) -> BaseResponse[bool]:
    # 解除拉黑
    return BaseResponse[bool].success(
        await get_container(request).contacts.unblock_friend(user_id, friend_id)
    )

# 处理好友申请
@router.post("/{userId}/application/{status}")
async def modify_application(
    payload: ModifyApplicationRequest,
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    status: Annotated[int, Path()],
) -> BaseResponse[NewSession | bool]:
    # 把传过来的字符串id转成int列表
    sender_ids = [int(value) for value in payload.receiveuser_ids]
    # 同意或拒绝好友申请；同意会生成会话
    result = await get_container(request).contacts.modify_application(
        user_id,
        sender_ids,
        status,
    )
    return BaseResponse[NewSession | bool].success(result)

# 好友详情
@router.get("/{userId}/friend/{friendId}")
async def friend_detail(
    request: Request,
    user_id: Annotated[int, Depends(bound_user_id)],
    friend_id: Annotated[int, Path(alias="friendId")],
) -> BaseResponse[FriendDetail]:
    # 查询单个好友详情
    return BaseResponse[FriendDetail].success(
        await get_container(request).contacts.detail(user_id, friend_id)
    )