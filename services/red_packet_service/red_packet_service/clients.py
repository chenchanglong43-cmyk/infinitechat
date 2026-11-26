"""Internal HTTP client from the red packet service to UserService."""
# 红包服务调用 user_service 内部接口的 HTTP 客户端
# 走的是服务间内部 API，要带服务令牌，不是给前端用的

from collections.abc import Awaitable, Callable  # 用来标注“异步解析函数”的类型
from typing import Any  # 通用类型：这里不关心具体结构，先按 Any 处理

import httpx  # 异步 HTTP 客户端
from infinitechat.contracts import ErrorCode  # 统一错误码枚举
from infinitechat.contracts.enums import UserState  # 用户状态：正常/封禁等
from infinitechat.contracts.errors import error_message  # 错误码 -> 给人看的提示文案

from infinitechat.contracts.http.internal import UserInfo  # 批量查用户时返回的用户信息模型

from infinitechat.foundation.security import ServiceIdentity, ServiceTokenCodec  # 服务身份 + 服务令牌签发工具

from red_packet_service.errors import RedPacketError  # 红包服务自己的异常类型

# 用户服务 HTTP 客户端：红包服务需要校验用户/群时都走这里
class UserServiceClient:
    def __init__(  # 构造方法: 创建对象时把依赖存起来
        self,
        http: httpx.AsyncClient,  # 全局复用的异步 HTTP 客户端
        resolve: Callable[[str], Awaitable[str]],  # 服务发现：给一个路由 key，返回 user_service 地址
        tokens: ServiceTokenCodec,  # 服务令牌签发器，用来证明“我是红包服务”
    ) -> None:
        self.http = http
        self.resolve = resolve
        self.tokens = tokens

    # 签发服务间鉴权头：每次请求都带一个“我是 RedPacketService”的令牌
    def headers(self) -> dict[str, str]:
        return {"X-Service-Token": self.tokens.issue(ServiceIdentity("RedPacketService"))}

    # 带服务发现的 GET 调用：所有“查一下”的接口都复用这段
    # routing_key 决定去哪个 user_service 实例，一般是 userId 或 sessionId
    async def _get(
        self,
        path: str,
        *,  # 星号后面的参数只能按名字传
        routing_key: str,
        params: Any,
    ) -> Any:
        # 先问服务发现要地址，再拼上具体路径发 GET
        response = await self.http.get(
            f"{await self.resolve(routing_key)}{path}",
            params=params,
            headers=self.headers(),
        )
        response.raise_for_status()  # 状态码不是 2xx 就报错（网络/网关层错误）
        payload = response.json()  # 统一响应壳子：{"code": 200, "data": ...}
        if payload.get("code") != 200:
            # 业务码不是 200，一律当成用户服务不可用，让上层转成服务异常
            raise RedPacketError(ErrorCode.SERVICE_UNAVAILABLE, "用户服务不可用")
        return payload.get("data")  # 真正有用的数据在 data 里

    # 查询用户状态：正常、封禁之类
    async def user_state(self, user_id: int) -> UserState | None:
        data = await self._get(
            "/api/internal/user/status",
            routing_key=str(user_id),  # 按用户 ID 路由，保证同一用户落到同一实例
            params={"userId": user_id},
        )
        # data 可能是 None；拿到的 state 是数字，转成 UserState 枚举方便比较
        state = data.get("state") if data else None
        return UserState(state) if state is not None else None

    # 校验单聊发红包权限：能不能给这个人发、是不是好友、有没有被拉黑等
    async def validate_direct(
        self,
        *,  # 星号后面的参数只能按名字传
        sender_id: int,
        receiver_id: int,
        session_id: int,
    ) -> tuple[bool, ErrorCode | None, str | None]:
        response = await self.http.post(
            f"{await self.resolve(str(sender_id))}/api/internal/validation/single-message",
            json={
                "senderId": sender_id,  # 发送人ID
                "receiverId": receiver_id,  # 接收人ID
                "sessionId": session_id,  # 会话ID
            },
            headers=self.headers(),
        )
        response.raise_for_status()  # 状态码不是 2xx 就报错
        envelope = response.json()  # 响应壳子：{"code": 200, "data": {...}}
        if envelope.get("code") != 200 or not envelope.get("data"):
            # 壳子不对或者没有 data，都当成用户服务不可用
            raise RedPacketError(ErrorCode.SERVICE_UNAVAILABLE, "用户服务不可用")
        data = envelope["data"]  # 校验结果：allowed + 可能的错误码
        # errorCode 可能为 null；有值就转成 ErrorCode 枚举
        code = ErrorCode(data["errorCode"]) if data.get("errorCode") is not None else None
        # 返回 (是否允许, 错误码, 错误提示)；允许时后两个都是 None
        return bool(data["allowed"]), code, error_message(code) if code is not None else None

    # 判断某个用户是不是某个群的成员
    async def group_membership(self, user_id: int, session_id: int) -> bool:
        data = await self._get(
            "/api/internal/group/isMember",
            routing_key=str(user_id),  # 按用户 ID 路由
            params={"userId": user_id, "sessionId": session_id},
        )
        # data 为空说明没查到，直接当成“不是成员”
        return bool(data and data["isMember"])

    # 查询群成员数：发红包时用来限制份数不能超过群人数
    async def group_member_count(self, session_id: int) -> int:
        data = await self._get(
            "/api/internal/group/memberCount",
            routing_key=str(session_id),  # 按群 ID 路由，同一个群的请求落到同一实例
            params={"sessionId": session_id},
        )
        return int(data["memberCount"])

    # 批量查询用户信息：一次请求拿一批人，避免循环里一个个查
    async def users(self, user_ids: set[int]) -> dict[int, UserInfo]:
        if not user_ids:
            return {}  # 空集合直接返回，省一次网络请求
        data = await self._get(
            "/api/internal/users/batch",
            # 用最小的 userId 当路由 key，保证同一批用户固定去同一个实例
            routing_key=str(min(user_ids)),
            # 用列表传重复参数，最终拼成 ?userIds=1&userIds=2&userIds=3
            params=[("userIds", str(user_id)) for user_id in sorted(user_ids)],
        )
        # 返回的 key 是字符串，转成 int；每个 value 用 UserInfo 校验并转成模型对象
        return {int(user_id): UserInfo.model_validate(info) for user_id, info in data.items()}