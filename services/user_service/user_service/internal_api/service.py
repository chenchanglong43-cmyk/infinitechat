"""Read-only user, membership and direct-message authorization."""  # 模块文档字符串：只读用户、群成员以及私聊消息权限校验服务

from infinitechat.contracts.enums import FriendStatus, GroupMemberRole, UserState  # 导入枚举：好友状态、群成员角色、用户状态
from infinitechat.contracts.errors import ErrorCode  # 导入错误码枚举
from infinitechat.contracts.http import (  # 导入http相关DTO
    GroupMemberCount,
    GroupMembership,
    MessageValidation,
    SingleMessageValidateRequest,
    UserStatus,
)

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # 导入SQLAlchemy异步会话、会话工厂

from user_service.integrations.redis import UserRedis  # 导入用户Redis缓存封装

from user_service.storage.repositories import ContactRepository, SessionRepository, UserRepository  # 导入数据仓库：联系人、会话、用户

_STATE_NAMES: dict[int, str] = {  # 状态名字(字典(整数 -> 字符串)): 3 个键的字典
    UserState.NORMAL: "正常",  # 用户状态 NORMAL: "正常"
    UserState.BANNED: "封禁",  # 用户状态 BANNED: "封禁"
    UserState.CANCELLED: "注销",  # 用户状态 CANCELLED: "注销"
}

# 内部校验服务
class InternalValidationService:
    # 初始化
    def __init__(  # 构造方法: 创建对象时把依赖存起来
        self,
        *,  # 星号后面的参数只能按名字传
        session_factory: async_sessionmaker[AsyncSession],
        cache: UserRedis,
    ) -> None:
        self._sessions = session_factory  # 赋值数据库会话工厂
        self._cache = cache  # 赋值Redis缓存实例

    # 用户状态
    async def user_status(self, user_id: int) -> UserStatus:
        async with self._sessions() as session:  # 开一个数据库会话, 用完自动关闭
            user = await UserRepository(session).get_by_id(user_id)  # 根据ID查询用户
            if user is None or user.is_delete:  # 用户不存在或者已经逻辑删除
                return UserStatus(user_id=user_id, state=None, state_name="用户不存在")
            return UserStatus(
                user_id=user_id,
                state=UserState(user.state),  # 转成枚举类型
                state_name=_STATE_NAMES.get(user.state, "未知"),  # 匹配状态文本，取不到就显示未知
            )

    # 群成员数量
    async def group_member_count(self, session_id: int) -> GroupMemberCount:
        async with self._sessions() as session:  # 开一个数据库会话, 用完自动关闭
            count = await SessionRepository(session).member_count(session_id)  # 查询该会话的成员总数
        return GroupMemberCount(session_id=session_id, member_count=count)  # 组装DTO返回

    # 群成员身份
    async def group_membership(self, user_id: int, session_id: int) -> GroupMembership:
        async with self._sessions() as session:  # 开一个数据库会话, 用完自动关闭
            membership = await SessionRepository(session).membership(user_id, session_id)  # 查询用户在会话里的成员记录
        return GroupMembership(
            user_id=user_id,
            session_id=session_id,
            is_member=membership is not None,  # 判断是否是成员
            role=GroupMemberRole(membership.role) if membership else None,  # 存在记录则转角色枚举，否则为空
        )

    # 校验单聊消息
    async def validate_single_message(
        self,
        request: SingleMessageValidateRequest,
    ) -> MessageValidation:
        async with self._sessions() as session:  # 开一个数据库会话, 用完自动关闭
            users = UserRepository(session)  # 实例化用户仓库
            sender = await users.get_by_id(request.sender_id)  # 查询发送方用户
            receiver = await users.get_by_id(request.receiver_id)  # 查询接收方用户
            if sender is None or sender.state != UserState.NORMAL or sender.is_delete:  # 发送方不存在、状态异常、已删除
                return self._reject(ErrorCode.SENDER_DISABLED)
            if receiver is None or receiver.state != UserState.NORMAL or receiver.is_delete:  # 接收方不存在、状态异常、已删除
                return self._reject(ErrorCode.RECEIVER_DISABLED)
            contacts = ContactRepository(session)  # 实例化联系人仓库
            sender_status = await self._friend_status(
                contacts,
                request.sender_id,
                request.receiver_id,
            )  # 查询发送方对接收方的好友关系
            if sender_status == FriendStatus.NON_FRIEND:  # 不是好友
                return self._reject(ErrorCode.NOT_FRIEND)
            if sender_status == FriendStatus.DELETED:  # 好友关系已删除
                return self._reject(ErrorCode.FRIEND_DELETED)
            receiver_status = await self._friend_status(
                contacts,
                request.receiver_id,
                request.sender_id,
            )  # 查询接收方对发送方的好友关系
            if receiver_status == FriendStatus.BLOCKED:  # 接收方拉黑了发送方
                return self._reject(ErrorCode.BLOCKED_BY_RECEIVER)
            session_id = await SessionRepository(session).direct_session(
                request.sender_id,
                request.receiver_id,
            )  # 查询两人对应的私聊会话ID
            if session_id != request.session_id:  # 会话ID不匹配，校验失败
                return self._reject(ErrorCode.VALIDATION_FAILED)
        return MessageValidation(allowed=True)  # 全部校验通过，允许发送

    # 查询好友状态
    async def _friend_status(
        self,
        contacts: ContactRepository,
        user_id: int,
        friend_id: int,
    ) -> FriendStatus:
        cached = await self._cache.get_friend_status(user_id, friend_id)  # 先读取Redis缓存
        if cached is not None:  # 缓存命中
            return FriendStatus(cached)
        relationship = await contacts.get_friend(user_id, friend_id)  # 缓存未命中，查数据库
        # 状态: relationship 时取 创建好友状态, 否则取 好友状态 NON_FRIEND
        status = FriendStatus(relationship.status) if relationship else FriendStatus.NON_FRIEND
        await self._cache.cache_friend_status(user_id, friend_id, status)  # 写入缓存
        return status  # 返回好友状态枚举

    @staticmethod  # 静态方法: 不需要 self
    def _reject(code: ErrorCode) -> MessageValidation:
        return MessageValidation(
            allowed=False,
            reject_reason=code.name,  # 错误码名称作为拒绝原因
            error_code=code,  # 填入错误码枚举
        )