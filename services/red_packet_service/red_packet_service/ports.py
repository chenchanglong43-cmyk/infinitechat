"""Ports the red packet service needs from UserService."""
# 红包服务要用的“端口”：只声明自己需要什么能力，不管对方怎么实现
# 这样红包服务不直接依赖 user_service 的代码，测试时也能塞一个假实现进去

from typing import Protocol  # Protocol：结构化接口，方法长得一样就算符合，不用显式继承

from infinitechat.contracts import ErrorCode  # 统一错误码，比如“不是好友”“群不存在”

from infinitechat.contracts.enums import SessionType, UserState  # 会话类型（单聊/群聊）、用户状态（正常/封禁等）

from infinitechat.contracts.http.internal import UserInfo  # 用户基础信息，批量查询时返回

# 用户校验端口：红包服务对“用户/会话校验”的需求都写在这
# 具体实现放到基础设施层，由它去调 user_service 的内部接口
class UserValidationPort(Protocol):
    # 查这个用户当前是什么状态；查不到返回 None
    async def user_state(self, user_id: int) -> UserState | None: ...

    # 校验“一个人能不能给另一个人发单聊红包”
    # 返回三元组：(是否通过, 失败错误码, 失败提示文案)
    # 通过时后两个是 None；不通过时由调用方决定怎么把错误抛给前端
    async def validate_direct(
        self,
        *,  # 星号后面的参数只能按名字传，避免三个 int 传错位置
        sender_id: int,
        receiver_id: int,
        session_id: int,
    ) -> tuple[bool, ErrorCode | None, str | None]: ...

    # 这个用户是不是这个群的成员
    async def group_membership(self, user_id: int, session_id: int) -> bool: ...

    # 这个群现在有多少人，用来校验红包份数不能超过群人数
    async def group_member_count(self, session_id: int) -> int: ...

    # 批量查用户信息：一次问一批人，避免在循环里一个个发请求
    async def users(self, user_ids: set[int]) -> dict[int, UserInfo]: ...

# 判断“这个用户有没有资格查看/领取这个红包”
# 单聊：只有发送人和接收人能看；群聊：得是群成员才行
async def can_access_packet(
    users: UserValidationPort,
    *,  # 星号后面的参数只能按名字传
    user_id: int,
    sender_id: int,
    receiver_id: int | None,
    session_id: int,
    session_type: int,
) -> bool:
    # 单聊红包：只认发送人和接收人这两个人；数据库里 session_type 存的是 int，所以转一下再比
    if session_type == int(SessionType.DIRECT):
        return user_id in {sender_id, receiver_id}
    # 其他情况（群聊）：是群成员就放行，不是就拒绝
    return await users.group_membership(user_id, session_id)