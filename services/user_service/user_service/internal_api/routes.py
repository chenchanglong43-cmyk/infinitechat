"""Service-token protected internal validation HTTP adapter."""  # 模块文档字符串：服务令牌保护的内部校验HTTP接口适配器

from typing import Annotated  # 导入Annotated，用于FastAPI类型注解

from fastapi import APIRouter, Depends, Query, Request  # 导入路由、依赖注入、查询参数、请求对象
from infinitechat.contracts import BaseResponse  # 导入统一返回体
from infinitechat.contracts.errors import ErrorCode  # 导入错误码枚举
from infinitechat.contracts.http import (  # 导入http相关DTO
    GroupMemberCount,
    GroupMembership,
    MessageValidation,
    SingleMessageValidateRequest,
    UserInfo,
    UserStatus,
)

from user_service.dependencies import get_container, require_internal_service  # 导入依赖工具：获取容器、校验内部服务令牌
from user_service.errors import BusinessError  # 导入业务自定义异常

router = APIRouter(prefix="/api/internal", tags=["internal"])  # 创建路由实例，统一内部接口前缀，标签归类internal
InternalCaller = Annotated[str, Depends(require_internal_service)]  # 内部caller: 字符串，依赖校验内部服务令牌

# 用户状态
@router.get("/user/status")  # 注册 GET 接口 /user/status
# 用户状态
async def user_status(
    request: Request,  # 请求对象
    user_id: Annotated[int, Query(alias="userId")],  # 查询参数，目标用户ID
    _caller: InternalCaller,  # 依赖注入，校验通过的内部调用方标识
) -> BaseResponse[UserStatus]:
    return BaseResponse[UserStatus].success(  # 封装成功响应
        await get_container(request).internal.user_status(user_id)  # 调用内部校验服务查询用户状态
    )

# 群成员数量
@router.get("/group/memberCount")  # 注册 GET 接口 /group/memberCount
# 群成员数量
async def member_count(
    request: Request,  # 请求对象
    session_id: Annotated[int, Query(alias="sessionId")],  # 查询参数，会话ID
    _caller: InternalCaller,  # 依赖注入，校验通过的内部调用方标识
) -> BaseResponse[GroupMemberCount]:
    return BaseResponse[GroupMemberCount].success(  # 封装成功响应
        await get_container(request).internal.group_member_count(session_id)  # 调用内部校验服务查询群成员数量
    )

# 群成员身份
@router.get("/group/isMember")  # 注册 GET 接口 /group/isMember
# 群成员身份
async def group_membership(
    request: Request,  # 请求对象
    user_id: Annotated[int, Query(alias="userId")],  # 查询参数，待校验用户ID
    session_id: Annotated[int, Query(alias="sessionId")],  # 查询参数，会话ID
    _caller: InternalCaller,  # 依赖注入，校验通过的内部调用方标识
) -> BaseResponse[GroupMembership]:
    return BaseResponse[GroupMembership].success(  # 封装成功响应
        await get_container(request).internal.group_membership(user_id, session_id)  # 调用内部校验服务查询用户在群内身份
    )

# 校验单聊消息
@router.post("/validation/single-message")  # 注册 POST 接口 /validation/single-message
# 校验单聊消息
async def validate_single_message(
    payload: SingleMessageValidateRequest,  # 请求体，单聊消息校验DTO
    request: Request,  # 请求对象
    _caller: InternalCaller,  # 依赖注入，校验通过的内部调用方标识
) -> BaseResponse[MessageValidation]:
    return BaseResponse[MessageValidation].success(  # 封装成功响应
        await get_container(request).internal.validate_single_message(payload)  # 调用内部校验服务校验私聊消息权限
    )

# 批量用户信息
@router.get("/users/batch")  # 注册 GET 接口 /users/batch
# 批量用户信息
async def batch_users(
    request: Request,  # 请求对象
    _caller: InternalCaller,  # 依赖注入，校验通过的内部调用方标识
) -> BaseResponse[dict[int, UserInfo]]:
    raw_values = request.query_params.getlist("userIds")  # 取出url参数里全部userIds原始值
    try:  # 下面这段可能出错, 出错交给 except 处理
        user_ids: list[int] = []  # 收集解析后的用户ID
        for raw_value in raw_values:
            for value in raw_value.split(","):  # 按逗号切成单个ID
                if not value:  # 跳过空字符串，比如末尾多余的逗号
                    continue
                user_ids.append(int(value))  # 每一项转成整数
    except ValueError as error:
        # 用户ID格式错误, 并带上原始异常
        raise BusinessError(ErrorCode.PARAMS_ERROR, "用户ID格式错误") from error
    return BaseResponse[dict[int, UserInfo]].success(  # 封装成功响应，key为用户ID，value为用户信息
        await get_container(request).profiles.batch_infos(user_ids)  # 调用用户资料服务批量查询用户信息
    )