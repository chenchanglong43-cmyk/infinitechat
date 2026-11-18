"""Service-token protected internal validation HTTP adapter."""
# 内部接口路由，只给别的微服务调用，靠服务令牌鉴权
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
# APIRouter：单独管理一组接口
# Depends：依赖注入，用来做鉴权
# Query：接收url问号后面的参数
# Request：拿到本次请求对象，用来拿全局容器
from infinitechat.contracts import BaseResponse
# 项目统一的返回包装类
from infinitechat.contracts.http import UserStatus
# 用户状态的DTO，规定返回的数据格式

from user_service.dependencies import get_container, require_internal_service
# get_container：拿到存各种资源的容器
# require_internal_service：鉴权依赖，校验服务令牌，非法直接报错

# 创建路由组，所有接口统一带上 /api/internal 前缀，文档标签标记internal
router = APIRouter(prefix="/api/internal", tags=["internal"])
# 把鉴权依赖封装一下，后面接口直接用，少写重复代码
InternalCaller = Annotated[str, Depends(require_internal_service)]

# 查询用户账号状态的内部接口
@router.get("/user/status")
async def user_status(
    request: Request,
    # 前端url参数叫userId，代码变量叫user_id，alias做名字映射
    user_id: Annotated[int, Query(alias="userId")],
    # 注入鉴权逻辑，令牌校验通过才往下走；返回的服务名这里不用，加下划线
    _caller: InternalCaller,
) -> BaseResponse[UserStatus]:
    return BaseResponse[UserStatus].success(
        await get_container(request).internal.user_status(user_id)
    )